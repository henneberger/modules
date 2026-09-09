"""Measure bounded candidate decoding over synthetic SQLite catalog rows.

Catalog insertion bypasses wheel publication intentionally; this measures discovery
query behavior, not publication integrity, wheel builds, or network transfer.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
import tracemalloc
from pathlib import Path

from module_families import registry as registry_module
from module_families.registry import Registry


def measure(work, members):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if (work / "repository").exists() or members < 100:
        raise ValueError("use a fresh work directory and at least 100 members")
    repository = Registry(work / "repository")
    started = time.perf_counter()
    for offset in range(0, members, 1000):
        records = []
        for index in range(offset, min(offset + 1000, members)):
            version = f"1.{index % 16}"
            card = {
                "family": "knowledge",
                "id": f"publisher.algorithm{index:09d}",
                "version": version,
                "summary": "Synthetic independently contributed algorithm",
                "provides": {"id": "knowledge.rank", "version": "1"},
                "capabilities": ["rank"],
                "effects": [],
            }
            records.append(
                (
                    "knowledge",
                    card["id"],
                    version,
                    json.dumps(card),
                    "synthetic-catalog",
                    "knowledge.rank",
                    "1",
                )
            )
        with repository._connect() as db:
            db.executemany(
                "INSERT INTO members(family,member,version,card,snapshot,provides_id,provides_version) VALUES(?,?,?,?,?,?,?)",
                records,
            )
    insertion_seconds = time.perf_counter() - started
    queries = {}
    original = registry_module.json.loads
    for name, arguments in [
        ("first_one", {"limit": 1}),
        ("first_hundred", {"limit": 100}),
        ("last_one", {"limit": 1, "offset": members - 1}),
        ("unmatched_specifier", {"limit": 1, "version_spec": ">=2"}),
    ]:
        decoded = 0

        def counted(value):
            nonlocal decoded
            decoded += 1
            return original(value)

        registry_module.json.loads = counted
        tracemalloc.start()
        started = time.perf_counter()
        try:
            result = repository.candidates("knowledge.rank", "1", **arguments)
            seconds = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            registry_module.json.loads = original
        queries[name] = {
            "arguments": arguments,
            "result_count": len(result),
            "decoded_cards": decoded,
            "seconds": seconds,
            "peak_traced_python_bytes": peak,
        }
    report = {
        "format": "module-families-candidate-paging-benchmark-1",
        "catalog_rows": members,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "synthetic_insertion_seconds": insertion_seconds,
        "database_bytes": repository.database.stat().st_size,
        "queries": queries,
        "limits": [
            "Synthetic catalog rows, not a million wheel publications or agents.",
            "Tracemalloc measures Python allocations during queries, not native SQLite page caches or process RSS.",
            "Arbitrary version predicates and deep offsets can scan scalar index entries; returned JSON decoding is bounded.",
        ],
    }
    (work / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, default=100000)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(measure(args.work_dir, args.members), indent=2))


if __name__ == "__main__":
    main()
