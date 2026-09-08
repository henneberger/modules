from __future__ import annotations

import copy
import json
import sys

import pytest

from module_families.authoring import draft_family
from module_families.catalog import Catalog, ManifestError
from module_families.cli import main
from module_families.manifest import write_manifest
from module_families.registry import Registry
from module_families.runtime import load


def cli(capsys, *arguments):
    result = main(list(arguments))
    captured = capsys.readouterr()
    assert result == 0, captured.err
    return json.loads(captured.out)


@pytest.mark.parametrize("layout", ["flat", "src"])
def test_generic_author_publish_lock_and_load(tmp_path, capsys, layout):
    project = tmp_path / "project"
    source = project / "src" if layout == "src" else project
    package = source / "independent_metrics"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "independent-metrics"\nversion = "0.3.0"\n'
        'description = "Small exact arithmetic operations"\n'
    )
    (project / "LICENSE").write_text("Test project license\n")
    (package / "__init__.py").write_text('"""Arithmetic values and operations."""\n')
    (package / "records.py").write_text(
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Measurement:\n"
        '    """An exact integer sum."""\n    total: int\n'
    )
    (package / "ops.py").write_text(
        "from collections.abc import Iterable\nfrom .records import Measurement\n"
        "def measure(values: Iterable[int]) -> Measurement:\n"
        '    """Sum a sequence of integer values."""\n    return Measurement(sum(values))\n'
    )
    manifest_path = tmp_path / "family.toml"
    initialized = cli(
        capsys,
        "init",
        str(project),
        "--family",
        "measurements",
        "--publisher",
        "metrics",
        "--package",
        "independent_metrics",
        "--out",
        str(manifest_path),
    )
    assert initialized["members"] == 2
    assert "independent_metrics" not in sys.modules
    assert cli(capsys, "validate", str(manifest_path))["valid"]
    draft = Catalog.load(manifest_path)
    assert draft.inspect("metrics.ops.measure")["effects"] == ["unknown"]
    assert draft.search("sum", allowed_effects=[]) == []

    document = draft.document
    document["context"]["solves"] = ["Aggregate exact integer measurements"]
    for member in document["members"]:
        member["effects"] = []
        if member["id"] == "metrics.ops.measure":
            member["version"] = "1.2.0"
    write_manifest(document, manifest_path)
    output = tmp_path / "build"
    built = cli(
        capsys,
        "build",
        str(manifest_path),
        "--member",
        "metrics.ops.measure",
        "--out",
        str(output),
    )
    assert built["members"] == 1
    assert built["artifacts"] == 3
    index = json.loads((output / "index.json").read_text())
    assert index["members"][0]["version"] == "1.2.0"
    assert all(
        all(requirement.startswith("mf-") for requirement in artifact["requires_dist"])
        for artifact in index["artifacts"]
    )
    assert "independent_metrics" not in sys.modules

    repository = tmp_path / "registry"
    published = cli(
        capsys, "publish", str(output / "index.json"), "--registry", str(repository)
    )
    assert published["added_members"] == 1
    lock_path = tmp_path / "selection.json"
    cli(
        capsys,
        "lock",
        "measurements",
        "metrics.ops.measure",
        "--member-version",
        "1.2.0",
        "--registry",
        str(repository),
        "--pure",
        "--out",
        str(lock_path),
    )
    lock = json.loads(lock_path.read_text())
    operation = load(Registry(repository), lock, tmp_path / "installed")
    assert operation([4, 5, 6]).total == 15
    assert "independent_metrics" not in sys.modules
    answer = cli(
        capsys,
        "run",
        str(lock_path),
        "--registry",
        str(repository),
        "--target",
        str(tmp_path / "installed"),
        "--args",
        "[[4, 5, 6]]",
    )
    assert answer == {"total": 15}


def test_authoring_does_not_import_or_execute_source(tmp_path):
    project = tmp_path / "project"
    package = project / "dangerous_if_imported"
    package.mkdir(parents=True)
    marker = tmp_path / "executed"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        "def operation(value):\n    return value\n"
    )
    manifest = tmp_path / "family.toml"
    draft_family(
        project,
        family="static-inventory",
        publisher="tests",
        package="dangerous_if_imported",
        destination=manifest,
    )
    assert not marker.exists()
    assert "dangerous_if_imported" not in sys.modules
    assert (
        Catalog.load(manifest).inspect("tests.operation")["symbol"]
        == "dangerous_if_imported:operation"
    )


@pytest.fixture
def manifest_document(tmp_path):
    package = tmp_path / "project" / "example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def first(value):\n    return value\n")
    manifest = tmp_path / "family.toml"
    draft_family(
        package.parent,
        family="example",
        publisher="tests",
        package="example",
        destination=manifest,
    )
    return Catalog.load(manifest).document


@pytest.mark.parametrize(
    "field, value",
    [
        ("family", "../invalid"),
        ("package", "invalid/package"),
        ("symbol", "another_namespace:first"),
        ("symbol", "example:invalid-name"),
        ("effects", ["unknown", "network"]),
    ],
)
def test_invalid_family_and_namespace_metadata(manifest_document, field, value):
    document = copy.deepcopy(manifest_document)
    if field == "family":
        document["name"] = value
    elif field == "package":
        document["source"]["package"] = value
    else:
        document["members"][0][field] = value
    with pytest.raises(ManifestError):
        Catalog(document)


def test_distribution_colliding_member_ids_are_rejected(manifest_document):
    first = manifest_document["members"][0]
    first["id"], first["local_id"] = "tests.some_name", "some_name"
    manifest_document["members"].append(
        {**first, "id": "tests.some-name", "local_id": "some-name"}
    )
    with pytest.raises(ManifestError, match="colliding"):
        Catalog(manifest_document)


def test_member_versions_survive_metadata_inspection(manifest_document):
    manifest_document["members"][0]["version"] = "2.1.0"
    catalog = Catalog(manifest_document)
    assert catalog.inspect("tests.first")["version"] == "2.1.0"
    assert catalog.search("first")[0]["version"] == "2.1.0"
