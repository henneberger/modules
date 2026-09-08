from __future__ import annotations

import json
import sys
import tomllib

import pytest

from module_families.manifest import (
    ManifestFormatError,
    dumps_toml,
    load_manifest,
    write_manifest,
)


def source_tree(tmp_path):
    package = tmp_path / "src" / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '"""Sample package."""\nfrom .rank import rank\n'
    )
    (package / "rank.py").write_text(
        'def rank(values):\n    """Order supplied values."""\n    return sorted(values)\n\ndef _private():\n    return None\n'
    )
    (package / "values.py").write_text(
        "from typing import Protocol\nclass Result:\n    pass\nclass Store(Protocol):\n    def read(self): ...\n"
    )
    (package / "_helpers.py").write_text(
        "def implementation_detail():\n    return None\n"
    )
    return package


def document():
    return {
        "schema_version": 1,
        "publisher": {"name": "sample"},
        "family": {
            "name": "sample",
            "version": "0.1.0",
            "description": "Sample operations",
        },
        "source": {"root": "src", "package": "sample", "license_files": []},
        "discovery": {"public": True, "include": ["**/*.py"], "exclude": []},
        "context": {"solves": ["Sample computations"]},
    }


def write_document(tmp_path, value=None):
    return write_manifest(value or document(), tmp_path / "family.toml")


def test_discovery_is_minimal_deterministic_and_excludes_private_sources(tmp_path):
    source_tree(tmp_path)
    path = write_document(tmp_path)
    first = load_manifest(path)
    assert first == load_manifest(path)
    assert [member["id"] for member in first["members"]] == [
        "sample.rank.rank",
        "sample.values.Result",
        "sample.values.Store",
    ]
    assert [member["kind"] for member in first["members"]] == [
        "operation",
        "type",
        "contract",
    ]
    assert first["members"][0] == {
        "id": "sample.rank.rank",
        "symbol": "sample.rank:rank",
        "publisher": "sample",
        "local_id": "rank.rank",
        "kind": "operation",
        "summary": "Order supplied values.",
        "effects": ["unknown"],
        "tags": ["rank"],
    }
    assert all(
        "source" not in member and "documentation" not in member
        for member in first["members"]
    )
    assert "[[members]]" not in path.read_text()
    assert first["source"]["root"] == "src"


def test_contribution_overrides_and_explicit_new_symbol(tmp_path):
    source_tree(tmp_path)
    contributions = tmp_path / "members"
    contributions.mkdir()
    (contributions / "rank.toml").write_text(
        '[[members]]\nid = "sample.rank.rank"\nkind = "algorithm"\neffects = []\nsolves = ["Sort a collection"]\n'
    )
    (contributions / "alias.toml").write_text(
        '[[members]]\nid = "sample.sort"\nsymbol = "sample.rank:rank"\nsummary = "Convenient sorting alias."\n'
    )
    value = document()
    value["contributions"] = {"include": ["members/**/*.toml", "members/rank.toml"]}
    path = write_document(tmp_path, value)
    loaded = load_manifest(path)
    members = {member["id"]: member for member in loaded["members"]}
    assert members["sample.rank.rank"]["kind"] == "algorithm"
    assert members["sample.rank.rank"]["effects"] == []
    assert members["sample.rank.rank"]["symbol"] == "sample.rank:rank"
    assert members["sample.sort"]["symbol"] == "sample.rank:rank"
    assert loaded["source"]["root"] == "src"


def test_root_overrides_are_partial_and_source_discovery_can_be_disabled(tmp_path):
    source_tree(tmp_path)
    value = document()
    value["members"] = [{"id": "sample.rank.rank", "effects": [], "version": "2.0.0"}]
    loaded = load_manifest(write_document(tmp_path, value))
    assert loaded["members"][0]["version"] == "2.0.0"
    value["discovery"]["public"] = False
    with pytest.raises(ManifestFormatError, match="has no symbol"):
        load_manifest(write_document(tmp_path, value))
    value["members"][0]["symbol"] = "sample.rank:rank"
    assert len(load_manifest(write_document(tmp_path, value))["members"]) == 1


def test_duplicate_explicit_ids_report_both_origins(tmp_path):
    source_tree(tmp_path)
    members = tmp_path / "members"
    members.mkdir()
    first, second = members / "a.toml", members / "b.toml"
    for path in (first, second):
        path.write_text('[[members]]\nid = "sample.rank.rank"\neffects = []\n')
    value = document()
    value["contributions"] = {"include": ["members/*.toml"]}
    with pytest.raises(ManifestFormatError) as error:
        load_manifest(write_document(tmp_path, value))
    assert "duplicate explicit member" in str(error.value)
    assert str(first) in str(error.value) and str(second) in str(error.value)


@pytest.mark.parametrize(
    "pattern",
    [
        "../outside.toml",
        "/tmp/outside.toml",
        "C:/outside.toml",
        "members/../../outside.toml",
    ],
)
def test_contribution_traversal_is_rejected(tmp_path, pattern):
    source_tree(tmp_path)
    value = document()
    value["contributions"] = {"include": [pattern]}
    with pytest.raises(ManifestFormatError, match="traversal"):
        load_manifest(write_document(tmp_path, value))


def test_outside_symlink_is_rejected_and_internal_alias_is_deduplicated(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source_tree(root)
    members = root / "members"
    members.mkdir()
    outside = tmp_path / "external.toml"
    outside.write_text('[[members]]\nid = "sample.rank.rank"\n')
    alias = members / "alias.toml"
    alias.symlink_to(outside)
    value = document()
    value["contributions"] = {"include": ["members/*.toml"]}
    with pytest.raises(ManifestFormatError, match="symlink"):
        load_manifest(write_document(root, value))
    alias.unlink()
    inside = members / "real.toml"
    inside.write_text('[[members]]\nid = "sample.rank.rank"\neffects = []\n')
    alias.symlink_to(inside)
    assert load_manifest(write_document(root, value))["members"][0]["effects"] == []


def test_empty_wildcard_allows_new_contributors_but_missing_exact_file_errors(tmp_path):
    source_tree(tmp_path)
    value = document()
    value["contributions"] = {"include": ["members/**/*.toml"]}
    assert len(load_manifest(write_document(tmp_path, value))["members"]) == 3
    value["contributions"]["include"] = ["members/missing.toml"]
    with pytest.raises(ManifestFormatError, match="must match"):
        load_manifest(write_document(tmp_path, value))


def test_discovery_includes_and_excludes_are_relative_to_package(tmp_path):
    source_tree(tmp_path)
    value = document()
    value["discovery"]["exclude"] = ["values.py", "future.py"]
    assert [
        row["id"] for row in load_manifest(write_document(tmp_path, value))["members"]
    ] == ["sample.rank.rank"]
    value["discovery"]["include"] = ["missing.py"]
    with pytest.raises(ManifestFormatError, match="must match"):
        load_manifest(write_document(tmp_path, value))


def test_discovery_never_imports_and_new_source_does_not_change_existing_cards(
    tmp_path,
):
    package = source_tree(tmp_path)
    marker = tmp_path / "executed"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    path = write_document(tmp_path)
    first = load_manifest(path)
    (package / "new.py").write_text("def another():\n    return 3\n")
    second = load_manifest(path)
    assert second["members"][1:] == first["members"]
    assert not marker.exists()
    assert "sample" not in sys.modules


def test_syntax_and_fragment_shape_errors_name_source(tmp_path):
    package = source_tree(tmp_path)
    path = write_document(tmp_path)
    (package / "rank.py").write_text("def broken(\n")
    with pytest.raises(ManifestFormatError, match="rank.py"):
        load_manifest(path)
    (package / "rank.py").write_text("def rank(): pass\n")
    path.write_text("[family\n")
    with pytest.raises(ManifestFormatError, match="cannot read TOML"):
        load_manifest(path)
    fragment = tmp_path / "bad.toml"
    fragment.write_text('[family]\nname = "bad"\n')
    value = document()
    value["contributions"] = {"include": ["bad.toml"]}
    with pytest.raises(ManifestFormatError, match="only.*members"):
        load_manifest(write_document(tmp_path, value))


def test_pyproject_tool_configuration_and_existing_tables_survive(tmp_path):
    source_tree(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\nname = "existing-project"\nversion = "4.0.0"\n[tool.ruff]\nline-length = 99\n'
    )
    write_manifest(document(), path)
    parsed = tomllib.loads(path.read_text())
    assert parsed["project"]["name"] == "existing-project"
    assert parsed["tool"]["ruff"]["line-length"] == 99
    loaded = load_manifest(path)
    assert loaded["name"] == "sample"
    assert len(loaded["members"]) == 3


def test_toml_serializer_roundtrips_nested_values_and_omits_none():
    value = document()
    value["context"] = {
        "quoted.key": 'A "quote"\nand a backslash \\',
        "absent": None,
        "enabled": True,
        "nested": {"cost": 1.5, "limits": [1, 2], "off": False},
    }
    value["dynamic_dependencies"] = {"sample.module:function": ["some-lib>=1"]}
    value["members"] = [
        {
            "id": "example",
            "symbol": "sample:example",
            "provides": {"id": "api", "version": "1"},
            "sharing": [["left.T", "right.T"]],
            "optional": None,
        }
    ]
    serialized = dumps_toml(value)
    parsed = tomllib.loads(serialized)
    assert "absent" not in parsed["context"]
    assert "optional" not in parsed["members"][0]
    assert parsed["members"][0]["provides"] == {"id": "api", "version": "1"}
    assert parsed["context"]["quoted.key"] == value["context"]["quoted.key"]
    assert serialized == dumps_toml(value)


def test_json_authoring_is_rejected_and_normalized_documents_roundtrip_as_toml(
    tmp_path,
):
    source_tree(tmp_path)
    expanded = load_manifest(write_document(tmp_path))
    rejected = tmp_path / "family.json"
    rejected.write_text(json.dumps(expanded))
    with pytest.raises(ManifestFormatError, match="must be TOML"):
        load_manifest(rejected)
    with pytest.raises(ManifestFormatError, match="must be TOML"):
        write_manifest(expanded, rejected)
    standalone = tmp_path / "standalone.toml"
    write_manifest(expanded, standalone)
    assert "[family]" in standalone.read_text()
    assert load_manifest(standalone) == expanded
