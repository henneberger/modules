"""Build a repository and synthesize programs from preserved real Python projects.

Run with an unused --work-dir. Pass --offline-environment and --wheelhouse
to additionally resolve, install, and execute an exact wheel environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from module_families.assemblies import instantiate, lock_assembly
from module_families.compiler import build_family
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.repository import open_repository
from module_families.synthesis import synthesize

ROOT = Path(__file__).resolve().parents[1]


def run(work: Path, *, offline_environment=False, wheelhouse=None) -> dict:
    work = work.resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    repository = open_repository(work / "repository")
    publications = []

    def publish(name, selected=None):
        destination = work / "build" / name
        index = build_family(
            ROOT / "families" / (name + ".toml"), destination, selected
        )
        result = repository.publish(destination / "index.json")
        publications.append({"source": name, "publisher": index["publisher"], **result})

    for name in (
        "iterators/interfaces",
        "iterators/more-itertools",
        "workflows/interfaces",
        "workflows/pipelines",
    ):
        publish(name)
    goal = ROOT / "examples/goals/deduplicate-and-batch.toml"
    initial = synthesize(goal, repository)
    assert initial["status"] == "unique" and len(initial["solutions"]) == 1
    first_lock = lock_assembly(initial, repository)

    # A new publisher contributes to the existing family, without replacing it.
    publish("iterators/boltons")
    resolution = synthesize(goal, repository)
    assert resolution["status"] == "ambiguous" and len(resolution["solutions"]) == 4
    (work / "programs.json").write_text(json.dumps(resolution, indent=2) + "\n")
    results = []
    for choice in range(4):
        assembly = lock_assembly(resolution, repository, choice=choice)
        instance = instantiate(assembly, repository, work / "programs")
        value = list(instance.module["run"]([3, 1, 3, 2, 1, 4], 2))
        assert value == [[3, 1], [2, 4]]
        results.append(
            {
                "choice": choice,
                "sha256": assembly["sha256"],
                "members": sorted(
                    binding["member"]["id"] for binding in assembly["bindings"].values()
                ),
                "result": value,
            }
        )
    # The earlier program remains replayable after the repository grows.
    original = instantiate(first_lock, repository, work / "programs")
    assert list(original.module["run"]([3, 1, 3, 2, 1, 4], 2)) == [[3, 1], [2, 4]]
    selected = lock_assembly(resolution, repository, choice=0)
    (work / "program.lock.json").write_text(json.dumps(selected, indent=2) + "\n")

    publish("mari/family", ["mari.algorithms.temporal.recency_decay"])
    freshness = synthesize(ROOT / "examples/goals/freshness.toml", repository)
    assert freshness["status"] == "unique"
    freshness_lock = lock_assembly(freshness, repository)
    (work / "freshness.lock.json").write_text(
        json.dumps(freshness_lock, indent=2) + "\n"
    )
    module = instantiate(freshness_lock, repository, work / "programs")
    value = module.module["decay"](90, method="exponential", half_life=90)
    assert value == 0.5
    environment = None
    if offline_environment:
        environment = lock_environment(
            selected,
            repository,
            work / "environment",
            find_links=[str(wheelhouse.resolve())] if wheelhouse else [],
            no_index=True,
        )
        lock_path = work / "environment/environment.lock.json"
        sync_environment(lock_path, work / "venv")
        replay = run_environment(
            lock_path, work / "venv", export="run", args=[[3, 1, 3, 2, 1, 4], 2]
        )
        assert replay["result"] == [[3, 1], [2, 4]]
        environment["replay"] = replay
    report = {
        "publications": publications,
        "solutions_before_new_publisher": 1,
        "solutions_after_new_publisher": 4,
        "programs": results,
        "old_program_replays": True,
        "mari_freshness": value,
        "loaded_upstream_packages": sorted(
            name
            for name in ("mark_kit", "more_itertools", "boltons", "module_workflows")
            if name in sys.modules
        ),
        "offline_environment": environment,
    }
    assert report["loaded_upstream_packages"] == []
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--offline-environment", action="store_true")
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    report = run(
        args.work_dir,
        offline_environment=args.offline_environment,
        wheelhouse=args.wheelhouse,
    )
    print(
        json.dumps(
            {
                "report": str(args.work_dir / "report.json"),
                "solutions": len(report["programs"]),
                "mari_freshness": report["mari_freshness"],
                "offline_replayed": report["offline_environment"] is not None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
