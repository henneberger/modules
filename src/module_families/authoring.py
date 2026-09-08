"""Generic source inventory for drafting a family; no candidate code executes."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from .catalog import Catalog


def draft_family(
    project: str | Path,
    *,
    family: str,
    publisher: str,
    package: str,
    destination: str | Path,
    version: str | None = None,
) -> dict:
    """Write a compact TOML family under an explicit publisher namespace."""
    project, destination = Path(project).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError(f"refusing to overwrite existing manifest: {destination}")
    if destination.suffix != ".toml":
        raise ValueError("manifest destination must end in .toml")
    from .manifest import dumps_toml

    config = project / "pyproject.toml"
    metadata = (
        tomllib.loads(config.read_text()).get("project", {}) if config.is_file() else {}
    )
    source_root = (
        project / "src"
        if (project / "src" / package.replace(".", "/")).is_dir()
        else project
    )
    dependencies = {}
    for requirement in metadata.get("dependencies", []):
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        if match:
            dependencies[match[0].replace("-", "_")] = requirement
    licenses = sorted(
        {
            path
            for pattern in ("LICENSE*", "COPYING*", "THIRD_PARTY_NOTICES*")
            for path in project.glob(pattern)
            if path.is_file()
        }
    )
    document = {
        "schema_version": 1,
        "publisher": {"name": publisher},
        "family": {
            "name": family,
            "version": version or metadata.get("version", "0.1.0"),
            "description": metadata.get("description")
            or f"Reusable components adapted from {package}.",
        },
        "source": {
            "root": os.path.relpath(source_root, destination.parent),
            "package": package,
            "license_files": [os.path.relpath(p, destination.parent) for p in licenses],
        },
        "discovery": {"public": True, "include": ["**/*.py"]},
        "contributions": {"include": ["members/**/*.toml"]},
        "context": {
            "metadata_status": "draft; add problem context and shared interface expectations"
        },
        "external_dependencies": dependencies,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        stream.write(
            "# Source is the inventory. Add semantic declarations in members/*.toml.\n"
        )
        stream.write(dumps_toml(document))
    try:
        catalog = Catalog.load(destination)
    except Exception:
        destination.unlink()
        raise
    return {
        "manifest": str(destination),
        "family": family,
        "members": len(catalog.document["members"]),
        "metadata_status": "draft",
        "next": "Review import-to-distribution mappings; scaffold a contribution for semantic declarations.",
    }


def scaffold_member(
    manifest: str | Path,
    member: str,
    destination: str | Path | None = None,
) -> dict:
    """Draft one declaration for existing software without copying its code or docs."""
    from .manifest import dumps_toml

    manifest = Path(manifest).resolve()
    if manifest.suffix != ".toml":
        raise ValueError("contribution scaffolding requires a TOML family")
    card = Catalog.load(manifest).inspect(member)
    destination = (
        Path(destination)
        if destination
        else manifest.parent / "members" / f"{card['local_id']}.toml"
    ).resolve()
    if not destination.is_relative_to(manifest.parent):
        raise ValueError("contribution must be inside the family manifest directory")
    if destination.suffix != ".toml":
        raise ValueError("contribution destination must end in .toml")
    config = tomllib.loads(manifest.read_text())
    if "tool" in config and "module-families" in config["tool"]:
        config = config["tool"]["module-families"]
    contribution = {
        "id": card["local_id"],
        "kind": card["kind"],
        "effects": card["effects"],
        "solves": card.get("solves", []),
        "use_when": card.get("use_when", []),
        "avoid_when": card.get("avoid_when", []),
    }
    for field in ("version", "provides", "requires", "sharing"):
        if field in card:
            contribution[field] = card[field]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        stream.write(f"# Existing source: {card.get('symbol', card.get('exports'))}\n")
        stream.write(
            "# Describe applicability and requirements; source remains authoritative.\n"
        )
        stream.write(dumps_toml({"members": [contribution]}))
    try:
        included = any(
            destination in [p.resolve() for p in manifest.parent.glob(pattern)]
            for pattern in config.get("contributions", {}).get("include", [])
        )
        if not included:
            raise ValueError(
                "destination must match the family's contributions.include patterns"
            )
        Catalog.load(manifest)
    except Exception:
        destination.unlink()
        raise
    return {
        "written": str(destination),
        "member": member,
        "source": card["symbol"],
        "metadata_status": "draft",
    }
