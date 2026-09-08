"""Reproduce reviewed iterator projections from the preserved upstream copies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from module_families.adaptation import adapt

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination: Path | None = None) -> dict:
    destination = destination or ROOT / "adaptations" / "generated"
    return {
        name: adapt(ROOT / "adaptations" / f"{name}.toml", destination / name)
        for name in ("more-itertools", "boltons")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    arguments = parser.parse_args()
    reports = prepare(arguments.destination)
    print(
        json.dumps(
            {
                name: {
                    "modules": len(report["modules"]),
                    "selected_declarations": sum(
                        len(module["selected_symbols"]) for module in report["modules"]
                    ),
                    "compiled_cells": report["compiled_cells"],
                    "unproved": report["unproved"],
                }
                for name, report in reports.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
