"""Bounded benchmark reports and atomic high-volume queue preparation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from module_families.coordination import CoordinationError, CoordinationQueue


def test_batch_immutable_identity_and_prerequisites(tmp_path):
    queue = CoordinationQueue(tmp_path / "queue.sqlite")
    records = [
        {"task_id": "first", "payload": {"task": 1}},
        {"task_id": "second", "payload": {"task": 2}, "prerequisites": ["first"]},
    ]
    result = queue.enqueue_many(records)
    assert len(result) == 2
    assert queue.enqueue_many(records) == result
    assert queue.claim("one")["id"] == "first"
    assert queue.claim("two") is None


def test_batch_failure_rolls_back_every_record(tmp_path):
    queue = CoordinationQueue(tmp_path / "queue.sqlite")
    with pytest.raises(CoordinationError, match="unknown task"):
        queue.enqueue_many(
            [
                {"task_id": "first", "payload": {}},
                {"task_id": "second", "payload": {}, "prerequisites": ["missing"]},
            ]
        )
    assert queue.list_tasks() == []
    with pytest.raises(CoordinationError, match="1 to 1000"):
        queue.enqueue_many([{"payload": {}}] * 1001)


def test_benchmark_uses_real_multiprocess_queue_and_bounded_summary(tmp_path):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/benchmark_coordination.py"),
            "--blocked-tasks",
            "10",
            "--tasks",
            "64",
            "--workers",
            "2",
            "--completions",
            "8",
            "--work-dir",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert report["blocked_frontier"]["blocked_high_priority_tasks"] == 10
    assert report["states"] == {"accepted": 9, "pending": 56}
    assert report["workload"]["task_records"] == 64
    assert report["workload"]["active_worker_processes"] == 2
    assert report["fencing"]["stale_submission_rejected"]
    assert report["completion"]["latency_sample_count"] == 8
    assert len(process.stdout) < 10000
    assert json.loads((tmp_path / "summary.json").read_text()) == report
