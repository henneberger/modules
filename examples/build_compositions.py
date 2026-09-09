from __future__ import annotations

import argparse
from pathlib import Path

from module_families.compiler import build_family
from module_families.module_build import build_module
from module_families.registry import Registry
from module_families.typed_program import build_program


def build(destination):
    root = Path(__file__).resolve().parent.parent
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("choose an empty output directory")
    registry = Registry(destination / "repository")
    families = {
        "knowledge-contracts": "examples/associated_program/interfaces.toml",
        "sqlite-providers": "examples/associated_program/providers.toml",
        "cached-query": "examples/associated_program/cached-parser.toml",
        "transaction-contracts": "families/transactions/interfaces.toml",
        "scoped-contract": "examples/typed/scoped-interfaces.toml",
    }
    for name, source in families.items():
        build_family(root / source, destination / name)
        registry.publish(destination / name / "index.json")
        print(f"Built {name}", flush=True)
    programs = {
        "search": "examples/associated_program/search.toml",
        "scoped-transaction": "examples/typed/scoped.toml",
    }
    for name, source in programs.items():
        result = build_program(root / source, registry, destination / name)
        registry.publish(result["index"])
        print(f"Built {name}", flush=True)
    for name in ("sqlite", "sqlite_view", "cached_search"):
        build_module(root / "examples/associated_program" / f"{name}.toml", registry, destination / name)
        print(f"Built {name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build reusable search, signature views, checked caching, and scoped transactions")
    parser.add_argument("--out", type=Path, required=True)
    build(parser.parse_args().out)
