"""Missing system contract -> independent graph candidates -> evaluated publication.

Both candidates compose existing Python providers. The negative candidate has
compatible APIs but wires ingestion and retrieval to different SQLite instances.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import tomllib
from pathlib import Path

from module_families.acceptance import accept_contribution
from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.contributions import (
    ContributionError,
    evaluate_contribution,
    prepare_contribution,
    verify_evidence,
)
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.handoffs import plan_contributions
from module_families.manifest import write_manifest
from module_families.module_build import build_module
from module_families.registry import Registry
from module_families.synthesis import synthesize

ROOT = Path(__file__).resolve().parents[1]


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def run(work, wheelhouse=None):
    work = Path(work).resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    shared = Registry(work / "repository")
    for name in ("interfaces", "mari", "adapters"):
        out = work / "base" / name
        build_family(ROOT / "families/knowledge" / (name + ".toml"), out)
        shared.publish(out / "index.json")
    retrieval = build_module(ROOT / "examples/modules/retrieval.toml", shared, work / "base/retrieval")
    shared.publish(Path(retrieval["index"]))
    goal = ROOT / "examples/goals/knowledge.toml"
    handoff = plan_contributions(goal, shared, work / "handoff")
    assert handoff["status"] == "needs-contributions"
    assert handoff["tasks"][0]["requires"] == {"id": "knowledge.system", "version": "1"}
    task = prepare_contribution(ROOT / "examples/contributions/knowledge.toml", shared, work / "task")
    assert not shared.candidates("knowledge.system", "1")
    task_path = work / "task/task.json"
    original = tomllib.loads((ROOT / "examples/modules/knowledge.toml").read_text())
    reports = {}
    # Each contributor gets its own staging repository and build. The accepted
    # repository does not acquire either candidate until the evaluation gate.
    for name in ("miswired", "integrator"):
        candidate_dir = work / "candidates" / name
        shutil.copytree(work / "repository", candidate_dir / "repository")
        candidate_repo = Registry(candidate_dir / "repository")
        graph = copy.deepcopy(original)
        graph["publisher"]["name"] = name
        if name == "miswired":
            graph["nodes"]["ingestion_data"] = copy.deepcopy(graph["nodes"]["data"])
            graph["links"]["harness.data"] = "ingestion_data"
            del graph["constraints"]["same_instance"]
        manifest = write_manifest(graph, candidate_dir / "system.toml")
        build = build_module(manifest, candidate_repo, candidate_dir / "build")
        candidate_repo.publish(Path(build["index"]))
        resolution = synthesize(goal, candidate_repo)
        assert resolution["status"] == "unique"
        assembly = lock_assembly(resolution, candidate_repo)
        _write(candidate_dir / "program.lock.json", assembly)
        environment = lock_environment(
            assembly, candidate_repo, candidate_dir / "environment",
            find_links=[str(Path(wheelhouse).resolve())] if wheelhouse else [],
            no_index=wheelhouse is not None,
        )
        sync_environment(environment["lock"], candidate_dir / "venv")
        evidence = evaluate_contribution(task_path, environment["lock"], candidate_dir / "venv")
        evidence_path = candidate_dir / "evidence.json"
        _write(evidence_path, evidence)
        result = {"evidence": str(evidence_path), "status": evidence["status"], "cases": evidence["cases"]}
        if name == "miswired":
            assert evidence["status"] == "failed"
            try:
                accept_contribution(task_path, environment["lock"], evidence_path, build["index"], shared)
            except ContributionError as error:
                result["rejection"] = str(error)
            else:
                raise AssertionError("miswired candidate was accepted")
            assert not shared.candidates("knowledge.system", "1")
        else:
            assert evidence["status"] == "passed"
            verify_evidence(task_path, environment["lock"], evidence_path)
            result["acceptance"] = accept_contribution(
                task_path, environment["lock"], evidence_path, build["index"], shared,
            )
            _write(candidate_dir / "acceptance.json", result["acceptance"])
        reports[name] = result
    after = plan_contributions(goal, shared, work / "after")
    assert after["status"] == "resolved" and not after["tasks"]
    final_resolution = synthesize(goal, shared)
    assert final_resolution["status"] == "unique"
    final = lock_assembly(final_resolution, shared)
    accepted = json.loads((work / "candidates/integrator/program.lock.json").read_text())
    assert final["sha256"] == accepted["sha256"]
    replay = run_environment(
        work / "candidates/integrator/environment/environment.lock.json",
        work / "candidates/integrator/venv", export="run",
        args=[{"guide:1": "modules"}, ["modules"], 1],
    )
    report = {
        "format": "module-families-contribution-example-1",
        "task_sha256": task["sha256"], "initial_status": handoff["status"],
        "candidates": reports, "final_status": after["status"],
        "accepted_program_sha256": final["sha256"], "replay": replay,
        "scope": "Two independently staged graph contributions using existing providers; local trusted evaluation, no autonomous agent scheduler.",
    }
    _write(work / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    result = run(args.work_dir, args.wheelhouse)
    print(json.dumps({"initial": result["initial_status"], "candidates": {key: value["status"] for key, value in result["candidates"].items()}, "final": result["final_status"], "result": result["replay"]["result"]}, indent=2))


if __name__ == "__main__":
    main()
