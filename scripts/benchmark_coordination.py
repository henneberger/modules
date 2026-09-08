"""Measure real queue operations on synthetic task records, not agent intelligence."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import platform
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from module_families.coordination import CoordinationError, CoordinationQueue

SAMPLE_LIMIT = 10000


def _percentiles(samples):
    values = sorted(samples)
    return {
        name: values[min(len(values) - 1, int((len(values) - 1) * percentile))]
        if values
        else None
        for name, percentile in [
            ("p50", 0.50),
            ("p95", 0.95),
            ("p99", 0.99),
            ("max", 1),
        ]
    }


def _worker(path, number, count):
    queue = CoordinationQueue(path)
    latencies = []
    claim_latencies = []
    for index in range(count):
        started = time.perf_counter()
        worker = f"process-{number}"
        task = queue.claim(worker, lease_seconds=300)
        if task is None:
            raise RuntimeError("benchmark exhausted eligible tasks")
        claimed = time.perf_counter()
        submitted = queue.submit(
            task["id"], worker, task["fence"], {"artifact_sha256": "0" * 64}
        )
        queue.accept(
            task["id"], submitted["submission_sha256"], {"benchmark": "accepted"}
        )
        if index < SAMPLE_LIMIT:
            claim_latencies.append(claimed - started)
            latencies.append(time.perf_counter() - started)
    return {"completed": count, "latencies": latencies, "claims": claim_latencies}


def _fencing(queue):
    queue.enqueue(
        {"benchmark": "abandoned-lease"}, task_id="crash-fence-probe", priority=2**62
    )
    abandoned = queue.claim("abandoned-worker", lease_seconds=0.005)
    time.sleep(0.01)
    recovered = queue.claim("replacement-worker", lease_seconds=60)
    assert (
        recovered["id"] == abandoned["id"] and recovered["fence"] > abandoned["fence"]
    )
    try:
        queue.submit(
            abandoned["id"], "abandoned-worker", abandoned["fence"], {"stale": True}
        )
    except CoordinationError:
        rejected = True
    else:
        raise AssertionError("expired worker was not fenced")
    submission = queue.submit(
        recovered["id"], "replacement-worker", recovered["fence"], {"recovered": True}
    )
    queue.accept(recovered["id"], submission["submission_sha256"], {"recovery": True})
    return {
        "abandoned_lease_reclaimed": True,
        "stale_submission_rejected": rejected,
        "old_fence": abandoned["fence"],
        "new_fence": recovered["fence"],
        "method": "real expired lease; no process kill or database crash injected",
    }


def measure_blocked_frontier(path, blocked_tasks=10000, completions=100):
    """Measure scheduling cost when higher-priority candidates are not ready."""
    if blocked_tasks < 1 or completions < 1:
        raise ValueError("blocked frontier and completions must be positive")
    if Path(path).exists():
        raise ValueError("blocked-frontier diagnostic requires a fresh database")
    queue = CoordinationQueue(path)
    queue.enqueue({}, task_id="prerequisite", priority=-1)
    for offset in range(0, blocked_tasks, 1000):
        queue.enqueue_many(
            [
                {
                    "payload": {},
                    "task_id": f"blocked-{i}",
                    "prerequisites": ["prerequisite"],
                    "priority": 1,
                }
                for i in range(offset, min(offset + 1000, blocked_tasks))
            ]
        )
    samples = []
    for index in range(completions):
        queue.enqueue({}, task_id=f"ready-{index}")
        started = time.perf_counter()
        task = queue.claim("worker")
        samples.append(time.perf_counter() - started)
        if task["id"] != f"ready-{index}":
            raise AssertionError("blocked task was incorrectly scheduled")
        submission = queue.submit(task["id"], "worker", task["fence"], {})
        queue.accept(task["id"], submission["submission_sha256"], {})
    return {
        "blocked_high_priority_tasks": blocked_tasks,
        "ready_tasks_completed": completions,
        "claim_seconds": _percentiles(samples),
    }


def measure(work, tasks=10000, workers=4, completions=1000, batch_size=1000):
    if (
        not 1 <= workers <= 64
        or not 1 <= completions <= tasks
        or not 1 <= batch_size <= 1000
    ):
        raise ValueError(
            "require 1–64 workers, 1 <= completions <= tasks, and 1–1000 batch size"
        )
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    path = work / "coordination.sqlite"
    if path.exists():
        raise ValueError("benchmark requires a fresh database path")
    queue = CoordinationQueue(path)
    payload = {
        "format": "coordination-benchmark-1",
        "contract_sha256": hashlib.sha256(b"shared benchmark contract").hexdigest(),
    }
    started = time.perf_counter()
    for offset in range(0, tasks, batch_size):
        queue.enqueue_many(
            [
                {"task_id": f"task-{index:09d}", "payload": payload}
                for index in range(offset, min(offset + batch_size, tasks))
            ]
        )
    enqueue_seconds = time.perf_counter() - started
    fencing = _fencing(queue)
    started = time.perf_counter()
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        futures = [
            pool.submit(
                _worker,
                str(path),
                number,
                completions // workers + (number < completions % workers),
            )
            for number in range(workers)
        ]
        results = [future.result() for future in futures]
    completion_seconds = time.perf_counter() - started
    with sqlite3.connect(path) as db:
        states = dict(db.execute("SELECT state,count(*) FROM tasks GROUP BY state"))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    completed = sum(item["completed"] for item in results)
    if states != {
        "accepted": completed + 1,
        **({"pending": tasks - completed} if tasks > completed else {}),
    }:
        raise AssertionError(f"unexpected queue state {states}")
    summary = {
        "format": "module-families-coordination-benchmark-1",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "machine": platform.machine(),
        },
        "workload": {
            "task_records": tasks,
            "additional_fencing_probe_records": 1,
            "active_worker_processes": workers,
            "worker_identity_count": workers,
            "additional_probe_identities": 2,
            "completed_workflows": completed,
            "batch_size": batch_size,
            "payload_bytes_per_record": len(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ),
            "payload_deduplication": False,
            "transport": "local SQLite API",
            "acceptance": "synthetic admin acceptance; no contribution evaluator or model calls",
        },
        "enqueue": {
            "seconds": enqueue_seconds,
            "records_per_second": tasks / enqueue_seconds,
        },
        "completion": {
            "seconds_including_process_startup": completion_seconds,
            "workflows_per_second": completed / completion_seconds,
            "claim_seconds": _percentiles(
                [v for item in results for v in item["claims"]]
            ),
            "claim_submit_accept_seconds": _percentiles(
                [v for item in results for v in item["latencies"]]
            ),
            "latency_sample_count": sum(len(item["latencies"]) for item in results),
            "sample_limit_per_worker": SAMPLE_LIMIT,
        },
        "fencing": fencing,
        "states": states,
        "database_bytes_after_checkpoint": path.stat().st_size,
        "limitations": [
            "Task records are not concurrent agents.",
            "No model inference, artifact transfer, evaluator, HTTP or TLS cost included.",
            "Small identical payloads reference one shared contract; payload text is stored per task.",
            "SQLite has one writer; results measure one local host, not distributed consensus or network partitions.",
        ],
    }
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--completions", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--blocked-tasks", type=int, default=0)
    args = parser.parse_args()
    summary = measure(
        args.work_dir, args.tasks, args.workers, args.completions, args.batch_size
    )
    if args.blocked_tasks:
        summary["blocked_frontier"] = measure_blocked_frontier(
            args.work_dir / "blocked.sqlite", args.blocked_tasks
        )
        (args.work_dir / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n"
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
