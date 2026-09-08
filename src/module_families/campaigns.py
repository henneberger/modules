"""Bounded, resumable campaigns of explicit executable contribution contracts."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections import deque
from pathlib import Path

from .assemblies import lock_assembly
from .contributions import prepare_contribution
from .synthesis import read_goal, synthesize
from .workers import _atomic_json, evaluate_submission, run_worker_once


class CampaignError(ValueError):
    pass


def _shape(value, allowed, required, label):
    if (
        not isinstance(value, dict)
        or set(value) - set(allowed)
        or set(required) - set(value)
    ):
        raise CampaignError(f"invalid {label} fields")


def read_campaign(path):
    path = Path(path).resolve()
    if path.suffix != ".toml":
        raise CampaignError("campaign authoring requires TOML")
    document = tomllib.loads(path.read_text())
    _shape(
        document,
        {"schema_version", "campaign", "tasks", "limits"},
        {"schema_version", "campaign", "tasks"},
        "campaign",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise CampaignError("campaign schema_version must be 1")
    config = document["campaign"]
    _shape(
        config, {"id", "goal", "driver"}, {"id", "goal", "driver"}, "campaign header"
    )
    if (
        any(
            not isinstance(value, str) or not value.strip() for value in config.values()
        )
        or len(config["id"]) > 100
    ):
        raise CampaignError("campaign id, goal and driver must be nonempty strings")
    tasks = document["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 1000:
        raise CampaignError("campaign requires 1..1000 explicit contribution tasks")
    known = {}
    for task in tasks:
        _shape(
            task,
            {"id", "manifest", "prerequisites", "max_attempts", "priority"},
            {"id", "manifest"},
            "task",
        )
        if (
            not isinstance(task["id"], str)
            or not task["id"]
            or len(task["id"]) > 100
            or task["id"] in known
            or not isinstance(task["manifest"], str)
        ):
            raise CampaignError("invalid or duplicate contribution task")
        task.setdefault("prerequisites", [])
        task.setdefault("max_attempts", 3)
        task.setdefault("priority", 0)
        if not isinstance(task["prerequisites"], list) or any(
            not isinstance(dep, str) for dep in task["prerequisites"]
        ):
            raise CampaignError("prerequisites must be task IDs")
        if (
            type(task["max_attempts"]) is not int
            or not 1 <= task["max_attempts"] <= 1000
            or type(task["priority"]) is not int
        ):
            raise CampaignError("invalid task attempt budget or priority")
        known[task["id"]] = task
    ordered, active, visited = [], set(), set()

    def visit(name):
        if name not in known:
            raise CampaignError("unknown prerequisite: " + name)
        if name in active:
            raise CampaignError("cyclic contribution prerequisites")
        if name in visited:
            return
        active.add(name)
        for dependency in known[name]["prerequisites"]:
            visit(dependency)
        active.remove(name)
        visited.add(name)
        ordered.append(known[name])

    for name in known:
        visit(name)
    document["tasks"] = ordered
    limits = document.setdefault("limits", {})
    defaults = {
        "rounds": 10,
        "workers_per_round": 1,
        "evaluations_per_round": 10,
        "driver_timeout": 300,
        "evaluation_timeout": 30,
        "lease_seconds": 60,
    }
    _shape(limits, defaults, set(), "limits")
    for key, default in defaults.items():
        limits.setdefault(key, default)
        if type(limits[key]) is not int or not 1 <= limits[key] <= (
            86400 if "timeout" in key or key == "lease_seconds" else 1000
        ):
            raise CampaignError("invalid campaign limit: " + key)
    return document


def run_campaign(
    path,
    queue,
    base,
    staging,
    *,
    work_root,
    evidence_store,
    evaluator_id,
    secret,
    trust_keys=None,
    find_links=(),
    no_index=False,
    driver_env=None,
):
    """Run bounded local driver/evaluator rounds over a dedicated local/HTTP queue.

    Worker invocations are sequential here. Independent remote workers may use
    the same service, but this coordinator is not a distributed process scheduler.
    """
    from .automation_cli import _repository_identity

    if base is staging or (
        hasattr(base, "root")
        and hasattr(staging, "root")
        and _repository_identity(str(base.root))
        == _repository_identity(str(staging.root))
    ):
        raise CampaignError("accepted and staging repositories must be distinct")
    if secret is None:
        raise CampaignError("an explicit evaluator key is required")
    path = Path(path).resolve()
    document = read_campaign(path)
    config, limits = document["campaign"], document["limits"]
    goal = read_goal(path.parent / config["goal"])
    driver_path = (path.parent / config["driver"]).resolve()
    driver_document = tomllib.loads(driver_path.read_text())
    _shape(driver_document, {"driver"}, {"driver"}, "driver file")
    _shape(driver_document["driver"], {"command"}, {"command"}, "driver")
    driver = driver_document["driver"]["command"]
    if (
        not isinstance(driver, list)
        or not driver
        or any(not isinstance(arg, str) or not arg for arg in driver)
    ):
        raise CampaignError("driver command must be a nonempty argument vector")
    driver = [arg.replace("{campaign}", str(path.parent)) for arg in driver]
    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ids = {task["id"]: config["id"] + ":" + task["id"] for task in document["tasks"]}
    # A campaign may not consume unrelated jobs through the general claim API.
    offset = 0
    while True:
        page = queue.list_tasks(limit=100, offset=offset)
        if any(record["id"] not in ids.values() for record in page):
            raise CampaignError(
                "campaign requires a dedicated queue; unrelated task found"
            )
        offset += len(page)
        if not page:
            break
    prepared = {}
    for task in document["tasks"]:
        directory = root / "contracts" / hashlib.sha256(task["id"].encode()).hexdigest()
        prepared[task["id"]] = prepare_contribution(
            path.parent / task["manifest"], base, directory
        )
    snapshot = {
        "format": "module-families-campaign-1",
        "document": document,
        "goal": goal,
        "driver": driver,
        "contracts": {name: bundle["sha256"] for name, bundle in prepared.items()},
    }
    snapshot_path = root / "campaign.json"
    if snapshot_path.exists() and json.loads(snapshot_path.read_text()) != snapshot:
        raise CampaignError("campaign workspace already binds different immutable work")
    _atomic_json(snapshot_path, snapshot)
    for task in document["tasks"]:
        queue.enqueue(
            payload=prepared[task["id"]],
            task_id=ids[task["id"]],
            prerequisites=[ids[dep] for dep in task["prerequisites"]],
            priority=task["priority"],
            max_attempts=task["max_attempts"],
        )
    events = deque(maxlen=100)
    event_count = 0

    def record_event(value):
        nonlocal event_count
        event_count += 1
        events.append(value)

    rounds = 0
    from .signing import PrivateEvaluatorKey

    public = secret.public_key() if isinstance(secret, PrivateEvaluatorKey) else secret
    trusted = dict(trust_keys or {})
    existing = trusted.get(evaluator_id, public)
    existing = (
        existing.public_key() if isinstance(existing, PrivateEvaluatorKey) else existing
    )
    if existing != public:
        raise CampaignError("evaluator key disagrees with campaign trust policy")
    trusted[evaluator_id] = public
    status = "round-limit"
    for rounds in range(1, limits["rounds"] + 1):
        for ordinal in range(limits["workers_per_round"]):
            result = run_worker_once(
                queue,
                base,
                staging,
                worker=f"{config['id']}-worker-{ordinal}",
                driver=driver,
                work_root=root / "workers",
                lease_seconds=limits["lease_seconds"],
                timeout=limits["driver_timeout"],
                driver_env=driver_env,
            )
            record_event({"round": rounds, "worker": result})
            if result["status"] == "idle":
                break
        # Use known task IDs; never evaluate unrelated work inserted concurrently.
        submitted = [queue.get(task_id=task_id) for task_id in ids.values()]
        submitted = [task for task in submitted if task["state"] == "submitted"]
        for task in submitted[: limits["evaluations_per_round"]]:
            try:
                outcome = evaluate_submission(
                    queue,
                    task["id"],
                    base,
                    staging,
                    work_root=root / "evaluations",
                    evidence_store=evidence_store,
                    evaluator_id=evaluator_id,
                    secret=secret,
                    find_links=find_links,
                    no_index=no_index,
                    timeout=limits["evaluation_timeout"],
                )
            except (ValueError, OSError, KeyError, TypeError) as error:
                outcome = {
                    "status": "evaluation-error",
                    "task_id": task["id"],
                    "error": str(error),
                }
            record_event({"round": rounds, "evaluation": outcome})
        records = [queue.get(task_id=task_id) for task_id in ids.values()]
        resolution = synthesize(
            goal, base, evidence_store=evidence_store, trust_keys=trusted
        )
        if all(record["state"] == "accepted" for record in records):
            status = (
                "complete"
                if resolution["status"] == "unique"
                else "needs-contributions"
            )
            break
        if any(record["state"] == "failed" for record in records):
            status = "blocked"
            break
    resolution = synthesize(
        goal, base, evidence_store=evidence_store, trust_keys=trusted
    )
    _atomic_json(root / "resolution.json", resolution)
    lock_path = None
    if status == "complete":
        lock_path = root / "program.lock.json"
        _atomic_json(lock_path, lock_assembly(resolution, base, trust_keys=trusted))
    records = [queue.get(task_id=task_id) for task_id in ids.values()]
    report = {
        "format": "module-families-campaign-result-1",
        "status": status,
        "rounds": rounds,
        "campaign": str(snapshot_path),
        "resolution": str(root / "resolution.json"),
        "program_lock": str(lock_path) if lock_path else None,
        "tasks": [
            {
                "id": r["id"],
                "state": r["state"],
                "attempts": r["attempts"],
                "error": r["error"],
            }
            for r in records
        ],
        "events": list(events),
        "event_count": event_count,
        "events_truncated": event_count > len(events),
    }
    _atomic_json(root / "report.json", report)
    return report
