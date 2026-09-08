"""Measure distributed declarations and selective builds on synthetic source.

This is a local build workload, not evidence of repository-scale performance.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from module_families.catalog import Catalog
from module_families.compiler import build_family, plan_build
from module_families.manifest import dumps_toml


def measure(work: Path, members: int, fragments: int) -> dict:
    package = work / "src" / "scale_fixture"
    declarations = work / "members"
    package.mkdir(parents=True)
    declarations.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_shared.py").write_text("def adjust(value):\n    return value * 2\n")
    cards = []
    for start in range(0, members, 100):
        module = f"batch{start // 100:04d}"
        source = ["from ._shared import adjust\n"]
        for number in range(start, min(start + 100, members)):
            name = f"operation{number:05d}"
            source.append(f"def {name}(value):\n    return adjust(value) + {number}\n")
            cards.append({"id": f"{module}.{name}", "kind": "algorithm", "effects": []})
        (package / f"{module}.py").write_text("\n".join(source))
    for contributor in range(fragments):
        (declarations / f"contributor{contributor:04d}.toml").write_text(
            dumps_toml({"members": cards[contributor::fragments]})
        )
    manifest = work / "family.toml"
    manifest.write_text(
        dumps_toml(
            {
                "schema_version": 1,
                "publisher": {"name": "benchmark"},
                "family": {
                    "name": "scale-fixture",
                    "version": "0.1.0",
                    "description": "Synthetic compiler workload",
                },
                "source": {"root": "src", "package": "scale_fixture"},
                "discovery": {"public": True},
                "contributions": {"include": ["members/*.toml"]},
                "context": {},
            }
        )
    )
    timings = {}
    started = time.perf_counter()
    catalog = Catalog.load(manifest)
    timings["discovery_seconds"] = time.perf_counter() - started
    chosen = "benchmark." + cards[0]["id"]
    started = time.perf_counter()
    plan = plan_build(manifest, member_ids=[chosen])
    timings["plan_seconds"] = time.perf_counter() - started
    if plan["status"] != "ready":
        raise ValueError(plan)
    output = work / "wheels"
    started = time.perf_counter()
    first = build_family(manifest, output, member_ids=[chosen])
    timings["selected_build_seconds"] = time.perf_counter() - started
    before = {
        artifact["filename"]: (output / artifact["filename"]).stat().st_mtime_ns
        for artifact in first["artifacts"]
    }
    started = time.perf_counter()
    second = build_family(manifest, output, member_ids=[chosen])
    timings["repeated_build_seconds"] = time.perf_counter() - started
    after = {filename: (output / filename).stat().st_mtime_ns for filename in before}
    if first["artifacts"] != second["artifacts"] or before != after:
        raise AssertionError("byte-identical cached wheels were changed")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "workload": "synthetic independent operations sharing one helper",
        "discovered_members": len(catalog.document["members"]),
        "contributor_files": fragments,
        "family_toml_lines": len(manifest.read_text().splitlines()),
        "family_toml_bytes": manifest.stat().st_size,
        "selected_member": chosen,
        "built_members": len(first["members"]),
        "built_artifacts": len(first["artifacts"]),
        "compiled_cells": first["build"]["compiled_cells"],
        "selected_cells": first["build"]["selected_cells"],
        "cached_wheels_unchanged": before == after,
        "timings": timings,
        "limits": [
            "Synthetic local workload; no network, concurrent writers, or agent task-success evaluation.",
            "Cold analysis parses the selected package scope; unchanged inputs reuse the persistent analysis cache, which is not a fine-grained incremental parser.",
            "Ten thousand trivial definitions do not establish support for arbitrary ecosystem packages.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, default=10000)
    parser.add_argument("--fragments", type=int, default=1000)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.fragments <= args.members:
        parser.error("require 1 <= fragments <= members")
    with tempfile.TemporaryDirectory(prefix="module-families-scale-") as temporary:
        report = measure(Path(temporary), args.members, args.fragments)
    text = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
