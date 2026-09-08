"""The real-project path remains executable after the repository grows."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_incremental_real_publishers_synthesize_and_replay(tmp_path):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable,
            str(root / "examples/real_world.py"),
            "--work-dir",
            str(tmp_path / "demo"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path / "demo/report.json").read_text())
    assert report["solutions_before_new_publisher"] == 1
    assert report["solutions_after_new_publisher"] == 4
    assert report["old_program_replays"]
    assert report["mari_freshness"] == 0.5
    assert report["loaded_upstream_packages"] == []
    assert len({program["sha256"] for program in report["programs"]}) == 4
    assert {publication["publisher"] for publication in report["publications"]} == {
        "interfaces",
        "more-itertools",
        "boltons",
        "pipelines",
        "mari",
    }
