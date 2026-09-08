from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from module_families.authoring import draft_family, scaffold_member
from module_families.catalog import Catalog
from module_families.cli import main
from module_families.compiler import build_family
from module_families.manifest import ManifestFormatError, load_manifest, write_manifest


def project(root: Path, count: int = 1):
    source = root / "src" / "contributed"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(
        "\n".join(
            f"def algorithm_{index}(value):\n    return value + {index}\n"
            for index in range(count)
        )
    )
    return source


def draft(root: Path, count: int = 1):
    project(root, count)
    manifest = root / "family.toml"
    result = draft_family(
        root,
        family="contributed",
        publisher="tests",
        package="contributed",
        destination=manifest,
    )
    return manifest, result


def cli(capsys, *args):
    result = main(list(args))
    captured = capsys.readouterr()
    assert result == 0, captured.err
    return json.loads(captured.out)


def test_compact_init_size_is_constant_with_one_or_thousand_definitions(tmp_path):
    small, small_result = draft(tmp_path / "small", 1)
    large, large_result = draft(tmp_path / "large", 1000)
    assert small_result["members"] == 1
    assert large_result["members"] == 1000
    assert small.read_text() == large.read_text()
    assert len(large.read_text().splitlines()) < 40
    assert "[[members]]" not in large.read_text()
    assert "algorithm_999" not in large.read_text()
    assert len(load_manifest(large)["members"]) == 1000


def test_cli_scaffold_is_automatically_included_without_changing_root(tmp_path, capsys):
    project(tmp_path)
    manifest = tmp_path / "family.toml"
    cli(
        capsys,
        "init",
        str(tmp_path),
        "--family",
        "contributed",
        "--publisher",
        "tests",
        "--package",
        "contributed",
        "--out",
        str(manifest),
    )
    original = manifest.read_bytes()
    result = cli(capsys, "scaffold", str(manifest), "--member", "tests.algorithm_0")
    contribution = Path(result["written"])
    assert contribution == tmp_path / "members" / "algorithm_0.toml"
    assert manifest.read_bytes() == original
    entry = tomllib.loads(contribution.read_text())["members"][0]
    assert entry["id"] == "algorithm_0"
    assert (
        "symbol" not in entry and "documentation" not in entry and "source" not in entry
    )
    assert Catalog.load(manifest).inspect("tests.algorithm_0")["effects"] == ["unknown"]
    cli(capsys, "validate", str(manifest))
    result = cli(
        capsys,
        "build",
        str(manifest),
        "--member",
        "tests.algorithm_0",
        "--out",
        str(tmp_path / "dist"),
    )
    assert result["members"] == 1
    assert result["artifacts"] == 2


def test_existing_contribution_is_never_truncated_and_duplicate_override_is_removed(
    tmp_path,
):
    manifest, _ = draft(tmp_path)
    first = Path(scaffold_member(manifest, "tests.algorithm_0")["written"])
    original = first.read_bytes()
    with pytest.raises(FileExistsError):
        scaffold_member(manifest, "tests.algorithm_0")
    assert first.read_bytes() == original
    duplicate = tmp_path / "members" / "another.toml"
    with pytest.raises(ManifestFormatError, match="duplicate explicit"):
        scaffold_member(manifest, "tests.algorithm_0", duplicate)
    assert not duplicate.exists()
    assert first.read_bytes() == original


def test_scaffold_rejects_unmatched_destinations_and_symlink_escape_before_writing(
    tmp_path,
):
    root = tmp_path / "root"
    manifest, _ = draft(root)
    unmatched = root / "not-a-contribution.toml"
    with pytest.raises(ValueError, match="must match"):
        scaffold_member(manifest, "tests.algorithm_0", unmatched)
    assert not unmatched.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "members").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        scaffold_member(manifest, "tests.algorithm_0")
    assert list(outside.iterdir()) == []


def test_drafting_does_not_import_source_or_overwrite_existing_manifest(tmp_path):
    source = project(tmp_path)
    marker = tmp_path / "executed"
    (source / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef operation():\n    return 1\n"
    )
    manifest = tmp_path / "family.toml"
    draft_family(
        tmp_path,
        family="contributed",
        publisher="tests",
        package="contributed",
        destination=manifest,
    )
    assert not marker.exists()
    assert "contributed" not in sys.modules
    original = manifest.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        draft_family(
            tmp_path,
            family="another",
            publisher="tests",
            package="contributed",
            destination=manifest,
        )
    assert manifest.read_bytes() == original


def test_pyproject_configuration_supports_scaffold_and_selected_build(tmp_path):
    project(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname = "contributed-project"\nversion = "0.1.0"\n')
    write_manifest(
        {
            "schema_version": 1,
            "publisher": {"name": "tests"},
            "family": {
                "name": "contributed",
                "version": "0.1.0",
                "description": "Contributed algorithms",
            },
            "source": {"root": "src", "package": "contributed"},
            "discovery": {"public": True},
            "contributions": {"include": ["members/**/*.toml"]},
        },
        manifest,
    )
    original = manifest.read_bytes()
    scaffold_member(manifest, "tests.algorithm_0")
    assert manifest.read_bytes() == original
    assert (
        tomllib.loads(manifest.read_text())["project"]["name"] == "contributed-project"
    )
    index = build_family(manifest, tmp_path / "dist", member_ids=["tests.algorithm_0"])
    assert len(index["members"]) == 1
    assert len(index["artifacts"]) == 2
    assert "contributed" not in sys.modules
