from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from module_families.compiler import build_family, plan_build
from module_families.manifest import ManifestFormatError, write_manifest


def fixture(tmp_path: Path, sources: dict[str, str], members: list[dict], **extra):
    root = tmp_path / "source" / "example"
    root.mkdir(parents=True)
    for filename, source in sources.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    manifest = tmp_path / "family.toml"
    write_manifest(
        {
            "schema_version": 1,
            "name": "example",
            "publisher": "fixture",
            "version": "0.1.0",
            "description": "Test family",
            "context": {},
            "source": {"root": "source", "package": "example"},
            "members": [
                {"kind": "operation", "summary": member["id"], **member}
                for member in members
            ],
            **extra,
        },
        manifest,
    )
    return manifest


def graph_fixture(tmp_path: Path):
    return fixture(
        tmp_path,
        {
            "__init__.py": "from .ops import compute as solve\n",
            "shared.py": "SCALE = 2\ndef helper(value):\n    return value * SCALE\n",
            "ops.py": (
                "from .shared import helper\n"
                "def compute(value):\n    return helper(value)\n"
                "def alternate(value):\n    return helper(value) + 1\n"
            ),
            "unrelated.py": (
                "def optional():\n    import absent_numpy\n"
                "    return absent_numpy.array([])\n"
            ),
        },
        [
            {"id": "fixture.solve", "symbol": "example:solve"},
            {"id": "fixture.alternate", "symbol": "example.ops:alternate"},
            {"id": "fixture.optional", "symbol": "example.unrelated:optional"},
        ],
        external_dependencies={"absent_numpy": "absent-numpy>=1"},
    )


def test_plan_explains_selected_source_closure_and_matches_build(tmp_path):
    manifest = graph_fixture(tmp_path)
    plan = plan_build(manifest, ["fixture.solve"])
    assert plan["status"] == "ready"
    assert plan["errors"] == []
    assert plan["counts"]["public_inventory"] == 3
    assert plan["counts"]["source_files"] == 4
    assert plan["counts"]["source_definitions"] == 5
    assert plan["counts"]["compiled_cells"] == 5
    assert plan["counts"]["reachable_cells"] == 3
    assert plan["counts"]["support_cells"] == 2
    assert plan["counts"]["omitted_cells"] == 2
    assert plan["external_requirements"] == []
    relationships = {item["symbol"]: item for item in plan["source_relationships"]}
    assert relationships["example.ops:compute"]["dependencies"] == [
        "example.shared:helper"
    ]
    assert relationships["example.shared:helper"]["dependencies"] == [
        "example.shared:SCALE"
    ]
    assert relationships["example.ops:compute"]["initialization_dependencies"] == []
    index = build_family(manifest, tmp_path / "output", ["fixture.solve"])
    assert {cell["distribution"] for cell in plan["cells"]} == {
        artifact["distribution"]
        for artifact in index["artifacts"]
        if artifact["distribution"].startswith("mf-cell-")
    }
    assert plan["counts"]["wheel_artifacts"] == len(index["artifacts"])
    # The result remains a portable, plain JSON review artifact.
    assert json.loads(json.dumps(plan)) == plan


def test_plan_shared_cells_and_external_requirements_are_per_selection(tmp_path):
    manifest = graph_fixture(tmp_path)
    shared = plan_build(manifest, ["fixture.solve", "fixture.alternate"])
    assert shared["counts"]["shared_cells"] == 2
    assert shared["counts"]["reachable_cells"] == 4
    assert shared["counts"]["wheel_artifacts"] == 6
    assert shared["external_requirements"] == []
    optional = plan_build(manifest, ["fixture.optional"])
    assert optional["counts"]["reachable_cells"] == 1
    assert optional["external_requirements"] == ["absent-numpy>=1"]
    assert optional["members"][0]["external_requirements"] == ["absent-numpy>=1"]


def test_plan_never_executes_source_or_writes_artifacts(tmp_path):
    marker = tmp_path / "must-not-exist"
    manifest = fixture(
        tmp_path,
        {
            "__init__.py": (
                "from pathlib import Path\n"
                f"INITIAL = Path({str(marker)!r}).write_text('executed')\n"
                "def selected():\n    return INITIAL\n"
            )
        },
        [{"id": "fixture.selected", "symbol": "example:selected"}],
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    plan = plan_build(manifest, ["fixture.selected"])
    assert plan["status"] == "ready"
    assert not marker.exists()
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
    initial = next(
        item
        for item in plan["source_relationships"]
        if item["symbol"] == "example:INITIAL"
    )
    assert initial["external_imports"] == [
        {"binding": "example:Path", "statement": "from pathlib import Path"}
    ]


def test_plan_reports_blocking_unsupported_source_and_unknown_selection(tmp_path):
    manifest = graph_fixture(tmp_path)
    unknown = plan_build(manifest, ["missing"])
    assert unknown["status"] == "blocked"
    assert "Unknown member IDs" in unknown["errors"][0]["message"]
    (tmp_path / "source/example/unrelated.py").write_text("print('top level')\n")
    blocked = plan_build(manifest, ["fixture.solve"])
    assert blocked["status"] == "blocked"
    assert "Unsupported top-level Expr" in blocked["errors"][0]["message"]
    assert "example.unrelated" in blocked["errors"][0]["message"]
    assert blocked["members"] == []
    assert blocked["counts"]["public_inventory"] == 3
    assert "entire source package" in blocked["limitations"][0]


def test_empty_selection_is_an_explicit_empty_plan(tmp_path):
    manifest = graph_fixture(tmp_path)
    plan = plan_build(manifest, [])
    assert plan["status"] == "ready"
    assert plan["counts"]["public_inventory"] == 3
    assert plan["counts"]["selected_members"] == 0
    assert plan["counts"]["reachable_cells"] == 0
    assert plan["cells"] == plan["members"] == []


def test_wheel_cache_compares_bytes_and_preserves_identical_files(tmp_path):
    manifest = graph_fixture(tmp_path)
    out = tmp_path / "output"
    first = build_family(manifest, out, ["fixture.solve"])
    originals = {}
    for artifact in first["artifacts"]:
        path = out / artifact["filename"]
        originals[path] = path.read_bytes()
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    second = build_family(manifest, out, ["fixture.solve"])
    assert first == second
    for path, content in originals.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == 1_000_000_000
    corrupt = next(iter(originals))
    corrupt.write_bytes(b"wrong cached content")
    third = build_family(manifest, out, ["fixture.solve"])
    assert first == third
    assert corrupt.read_bytes() == originals[corrupt]


def test_unrelated_edit_preserves_selected_wheels_shared_edit_changes_closure(tmp_path):
    manifest = graph_fixture(tmp_path)
    out = tmp_path / "output"
    first = build_family(manifest, out, ["fixture.solve"])
    originals = {
        artifact["filename"]: (out / artifact["filename"]).read_bytes()
        for artifact in first["artifacts"]
    }
    source = tmp_path / "source/example/ops.py"
    source.write_text(
        source.read_text().replace("helper(value) + 1", "helper(value) + 2")
    )
    second = build_family(manifest, out, ["fixture.solve"])
    assert first["members"] == second["members"]
    assert first["artifacts"] == second["artifacts"]
    assert first["build"]["source_digest"] != second["build"]["source_digest"]
    for filename, content in originals.items():
        assert (out / filename).read_bytes() == content
    shared = tmp_path / "source/example/shared.py"
    shared.write_text(shared.read_text().replace("SCALE = 2", "SCALE = 3"))
    third = build_family(manifest, out, ["fixture.solve"])
    assert first["members"][0]["sha256"] != third["members"][0]["sha256"]
    assert {
        item["distribution"]
        for item in first["artifacts"]
        if item["distribution"].startswith("mf-cell-")
    }.isdisjoint(
        {
            item["distribution"]
            for item in third["artifacts"]
            if item["distribution"].startswith("mf-cell-")
        }
    )


def test_toml_discovery_keeps_selected_artifacts_stable_when_sibling_lines_shift(
    tmp_path,
):
    package = tmp_path / "src/example"
    package.mkdir(parents=True)
    source = package / "__init__.py"
    source.write_text(
        "SCALE = 2\n"
        "def _helper(value):\n    return value * SCALE\n"
        "def unrelated():\n    return 0\n"
        "def answer(value):\n    return _helper(value)\n"
    )
    manifest = tmp_path / "family.toml"
    manifest.write_text(
        "schema_version = 1\n[publisher]\nname = 'fixture'\n"
        "[family]\nname = 'example'\nversion = '0.1.0'\ndescription = 'A library'\n"
        "[source]\nroot = 'src'\npackage = 'example'\n"
        "[discovery]\npublic = true\n"
        "[[members]]\nid = 'answer'\nversion = '0.2.0'\n"
    )
    plan = plan_build(manifest, ["fixture.answer"])
    assert plan["status"] == "ready"
    assert plan["counts"]["public_inventory"] == 2
    assert plan["counts"]["reachable_cells"] == 3
    assert plan["members"][0]["version"] == "0.2.0"
    first = build_family(manifest, tmp_path / "first", ["fixture.answer"])
    source.write_text(
        source.read_text().replace("return 0", "other = 1\n    return other + 1")
    )
    second = build_family(manifest, tmp_path / "second", ["fixture.answer"])
    assert first["members"] == second["members"]
    assert first["artifacts"] == second["artifacts"]
    for artifact in first["artifacts"]:
        assert (tmp_path / "first" / artifact["filename"]).read_bytes() == (
            tmp_path / "second" / artifact["filename"]
        ).read_bytes()
    source.write_text(source.read_text().replace("SCALE = 2", "SCALE = 3"))
    third = build_family(manifest, tmp_path / "third", ["fixture.answer"])
    assert first["members"][0]["sha256"] != third["members"][0]["sha256"]


def test_discovery_exclusions_limit_publication_roots_not_source_analysis(tmp_path):
    package = tmp_path / "src/example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def selected():\n    return 1\n")
    (package / "excluded.py").write_text("print('unsupported initialization')\n")
    manifest = tmp_path / "family.toml"
    manifest.write_text(
        "schema_version = 1\n[publisher]\nname = 'fixture'\n"
        "[family]\nname = 'example'\nversion = '0.1.0'\ndescription = 'A library'\n"
        "[source]\nroot = 'src'\npackage = 'example'\n"
        "[discovery]\npublic = true\nexclude = ['excluded.py']\n"
    )
    plan = plan_build(manifest, ["fixture.selected"])
    assert plan["counts"]["public_inventory"] == 1
    assert plan["status"] == "blocked"
    assert "example.excluded" in plan["errors"][0]["message"]


@pytest.mark.parametrize(
    "invalid, message",
    [
        ({"kind": "typo"}, "invalid kind"),
        ({"effects": "cpu"}, "effects must be a list"),
    ],
)
def test_invalid_member_metadata_blocks_plan_and_build_before_writes(
    tmp_path, invalid, message
):
    manifest = fixture(
        tmp_path,
        {"__init__.py": "def selected():\n    return 1\n"},
        [{"id": "fixture.selected", "symbol": "example:selected", **invalid}],
    )
    plan = plan_build(manifest, ["fixture.selected"])
    assert plan["status"] == "blocked"
    assert message in plan["errors"][0]["message"]
    out = tmp_path / "output"
    with pytest.raises(ManifestFormatError, match=message):
        build_family(manifest, out, ["fixture.selected"])
    assert not out.exists()
