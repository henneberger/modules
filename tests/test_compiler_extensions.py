from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import zipfile

import pytest

from module_families import compiler
from module_families.compiler import (
    CompilationError,
    build_family,
    compile_sources,
    plan_build,
)
from module_families.manifest import write_manifest


def write_project(tmp_path, files, members, **extra):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    document = {
        "schema_version": 1,
        "name": "composed",
        "version": "0.1.0",
        "description": "Independent package composition",
        "publisher": "tests",
        "context": {},
        "members": members,
        **extra,
    }
    path = tmp_path / "family.toml"
    write_manifest(document, path)
    return path


def run_installed(tmp_path, out, index, code):
    site = tmp_path / "site"
    site.mkdir()
    for artifact in index["artifacts"]:
        with zipfile.ZipFile(out / artifact["filename"]) as wheel:
            wheel.extractall(site)
    imports = {card["local_id"]: card["import_module"] for card in index["members"]}
    script = (
        f"import sys, importlib\nsys.path.insert(0, {str(site)!r})\nmodules = {{key: importlib.import_module(value) for key, value in {imports!r}.items()}}\n"
        + code
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_grouped_exports_and_standalone_type_share_one_identity(tmp_path):
    manifest = write_project(
        tmp_path,
        {
            "src/example/__init__.py": "",
            "src/example/data.py": "from dataclasses import dataclass\n@dataclass\nclass Receipt:\n    amount: int\n",
            "src/example/ops.py": "from .data import Receipt\ndef charge(amount):\n    return Receipt(amount)\ndef accept(receipt):\n    return isinstance(receipt, Receipt)\n",
        },
        [
            {
                "id": "processor",
                "kind": "module",
                "summary": "Processor",
                "exports": {
                    "Receipt": "example.data:Receipt",
                    "charge": "example.ops:charge",
                    "accept": "example.ops:accept",
                },
            },
            {
                "id": "receipt",
                "kind": "type",
                "summary": "Shared type",
                "symbol": "example.data:Receipt",
            },
        ],
        source={"root": "src", "package": "example"},
    )
    out = tmp_path / "dist"
    index = build_family(manifest, out)
    assert len(index["artifacts"]) == 5
    assert index["members"][0]["export"] == "value"
    plan = plan_build(manifest)
    assert plan["status"] == "ready"
    assert len(plan["members"][0]["root_cells"]) == 3
    assert plan["counts"]["shared_cells"] == 1
    run_installed(
        tmp_path,
        out,
        index,
        """
processor = modules['processor'].value
receipt_type = modules['receipt'].value
assert processor['Receipt'] is receipt_type
assert type(processor['charge'](5)) is receipt_type
assert processor['accept'](receipt_type(2))
assert modules['processor'].charge is processor['charge']
assert 'example' not in sys.modules
""",
    )


def test_grouped_reserved_facade_names_remain_accessible_in_mapping(tmp_path):
    manifest = write_project(
        tmp_path,
        {"src/example/__init__.py": "def answer():\n    return 42\n"},
        [
            {
                "id": "reserved",
                "kind": "module",
                "summary": "Aliases",
                "exports": {
                    "value": "example:answer",
                    "__all__": "example:answer",
                    "_mf_export_0": "example:answer",
                },
            },
        ],
        source={"root": "src", "package": "example"},
    )
    out = tmp_path / "dist"
    index = build_family(manifest, out)
    run_installed(
        tmp_path,
        out,
        index,
        "assert {fn() for fn in modules['reserved'].value.values()} == {42}\n",
    )


def test_multi_source_cross_package_imports_preserve_shared_types_and_licenses(
    tmp_path,
):
    manifest = write_project(
        tmp_path,
        {
            "one/core/__init__.py": "class Receipt:\n    def __init__(self, amount):\n        self.amount = amount\n",
            "two/processor/__init__.py": "from core import Receipt\ndef charge(amount):\n    return Receipt(amount)\n",
            "one/LICENSE": "Core license",
            "two/LICENSE": "Processor license",
        },
        [
            {
                "id": "processor",
                "kind": "module",
                "summary": "Cross-package module",
                "exports": {"Receipt": "core:Receipt", "charge": "processor:charge"},
            }
        ],
        sources={
            "core": {
                "root": "one",
                "package": "core",
                "license_files": ["one/LICENSE"],
            },
            "adapter": {
                "root": "two",
                "package": "processor",
                "license_files": ["two/LICENSE"],
            },
        },
    )
    out = tmp_path / "dist"
    index = build_family(manifest, out)
    assert len(index["artifacts"]) == 3
    assert all(
        "core" not in artifact["requires_dist"] for artifact in index["artifacts"]
    )
    with zipfile.ZipFile(out / index["artifacts"][0]["filename"]) as archive:
        licenses = {
            name.rsplit("/", 1)[-1]
            for name in archive.namelist()
            if "/licenses/" in name
        }
    assert licenses == {"core-LICENSE", "adapter-LICENSE"}
    run_installed(
        tmp_path,
        out,
        index,
        """
module = modules['processor'].value
assert type(module['charge'](8)) is module['Receipt']
assert not {'core', 'processor'} & set(sys.modules)
""",
    )


def test_cross_source_recursion_is_one_component(tmp_path):
    sources = {
        "a": {"root": tmp_path / "one", "package": "alpha"},
        "b": {"root": tmp_path / "two", "package": "beta"},
    }
    for alias, source in sources.items():
        root = source["root"] / source["package"]
        root.mkdir(parents=True)
        other = "beta" if alias == "a" else "alpha"
        (root / "__init__.py").write_text(
            f"from {other} import answer as other\ndef answer(n):\n    return {alias!r} if n == 0 else other(n - 1)\n"
        )
    compiled = compile_sources(sources, "recursive")
    assert len(compiled.cells) == 1
    assert (
        compiled.symbols["alpha:answer"]["cell"]
        == compiled.symbols["beta:answer"]["cell"]
    )
    assert (
        compiled.symbols["alpha:answer"]["export"]
        != compiled.symbols["beta:answer"]["export"]
    )


def test_interface_only_build_needs_no_source_or_wheels(tmp_path):
    interface = {
        "id": "payments.receipt",
        "version": "1",
        "types": ["Receipt"],
        "callables": {},
    }
    manifest = write_project(tmp_path, {}, [], interfaces=[interface])
    stats = {}
    out = tmp_path / "dist"
    index = build_family(manifest, out, stats=stats)
    assert index["interfaces"] == [interface]
    assert index["members"] == index["artifacts"] == []
    assert index["build"]["compiled_cells"] == 0
    assert stats["analysis_cache"] == "not-applicable"
    assert not list(out.rglob("*.whl"))
    assert plan_build(manifest)["interfaces"] == [interface]


def cache_fixture(tmp_path):
    return write_project(
        tmp_path,
        {
            "src/example/__init__.py": "def answer():\n    return 42\ndef unused():\n    return 1\n"
        },
        [
            {
                "id": "answer",
                "kind": "algorithm",
                "summary": "Answer",
                "symbol": "example:answer",
            },
        ],
        source={"root": "src", "package": "example"},
    )


def test_persistent_cache_skips_analysis_and_preserves_exact_index(
    tmp_path, monkeypatch
):
    manifest = cache_fixture(tmp_path)
    out = tmp_path / "dist"
    initial_stats = {}
    first = build_family(manifest, out, stats=initial_stats)
    assert initial_stats["analysis_cache"] == "miss"

    def forbidden(*args):
        raise AssertionError("Unchanged analysis must load its persistent graph")

    monkeypatch.setattr(compiler._Compiler, "read", forbidden)
    monkeypatch.setattr(compiler._Compiler, "analyze", forbidden)
    monkeypatch.setattr(compiler._Compiler, "emit", forbidden)
    warm_stats = {}
    second = build_family(manifest, out, stats=warm_stats)
    assert warm_stats["analysis_cache"] == "hit"
    assert first == second
    assert json.loads((out / "build-stats.json").read_text()) == warm_stats
    assert plan_build(manifest, cache_dir=out / ".cache")["status"] == "ready"


def test_cache_invalidation_is_coarse_but_selected_wheels_stay_stable(tmp_path):
    manifest = cache_fixture(tmp_path)
    out = tmp_path / "dist"
    first_stats = {}
    first = build_family(manifest, out, stats=first_stats)
    source = tmp_path / "src/example/__init__.py"
    source.write_text(source.read_text().replace("return 1", "return 2"))
    second_stats = {}
    second = build_family(manifest, out, stats=second_stats)
    assert second_stats["analysis_cache"] == "miss"
    assert second_stats["analysis_key"] != first_stats["analysis_key"]
    assert first["artifacts"] == second["artifacts"]
    source.write_text(source.read_text().replace("return 42", "return 43"))
    third = build_family(manifest, out)
    assert first["members"][0]["sha256"] != third["members"][0]["sha256"]


@pytest.mark.parametrize(
    "change", ["external", "dynamic", "compiler", "added_file", "removed_file"]
)
def test_analysis_cache_keys_cover_actual_analysis_inputs(
    tmp_path, monkeypatch, change
):
    root = tmp_path / "example"
    root.mkdir()
    (root / "__init__.py").write_text("def answer():\n    return 42\n")
    extra = root / "extra.py"
    extra.write_text("def unrelated():\n    return 1\n")
    kwargs = {"cache_dir": tmp_path / "cache", "stats": {}}
    compiler.compile_project(tmp_path, "example", "example", **kwargs)
    original = kwargs["stats"]["analysis_key"]
    if change == "external":
        kwargs["external_dependencies"] = {"numpy": "numpy>=2"}
    elif change == "dynamic":
        kwargs["dynamic_dependencies"] = {"example:answer": ["numpy>=2"]}
    elif change == "compiler":
        monkeypatch.setattr(compiler, "COMPILER_FORMAT", "changed-compiler")
    elif change == "added_file":
        (root / "added.py").write_text("VALUE = 1\n")
    else:
        extra.unlink()
    compiler.compile_project(tmp_path, "example", "example", **kwargs)
    assert kwargs["stats"]["analysis_cache"] == "miss"
    assert kwargs["stats"]["analysis_key"] != original


@pytest.mark.parametrize("corruption", ["[]", "not JSON", '{"schema_version": 1}'])
def test_corrupt_analysis_cache_is_rebuilt_from_source(tmp_path, corruption):
    manifest = cache_fixture(tmp_path)
    out = tmp_path / "dist"
    first = build_family(manifest, out)
    cache = next((out / ".cache").glob("analysis-*.json"))
    cache.write_text(corruption)
    stats = {}
    assert build_family(manifest, out, stats=stats) == first
    assert stats["analysis_cache"] == "corrupt"
    assert "project" in json.loads(cache.read_text())


def test_explicit_module_scope_skips_unrelated_initializers_and_rejects_missing_edges(
    tmp_path,
):
    manifest = write_project(
        tmp_path,
        {
            "src/example/__init__.py": "from .ops import *\n",
            "src/example/ops.py": "def answer():\n    return 42\n",
            "src/example/unrelated.py": "print('unsupported initializer')\n",
            "src/example/support.py": "VALUE = 43\n",
        },
        [
            {
                "id": "answer",
                "kind": "algorithm",
                "summary": "Answer",
                "symbol": "example.ops:answer",
            }
        ],
        source={"root": "src", "package": "example", "modules": ["example.ops"]},
    )
    out = tmp_path / "dist"
    first = build_family(manifest, out)
    assert first["build"]["compiled_cells"] == 1
    (tmp_path / "src/example/ops.py").write_text(
        "from .support import VALUE\ndef answer():\n    return VALUE\n"
    )
    with pytest.raises(CompilationError, match="Unresolved"):
        build_family(manifest, out)
    document = tomllib.loads(manifest.read_text())
    document["source"]["modules"].append("example.support")
    write_manifest(document, manifest)
    assert build_family(manifest, out)["build"]["compiled_cells"] == 2


def test_package_ownership_must_be_unambiguous(tmp_path):
    with pytest.raises(CompilationError, match="overlapping source package"):
        compile_sources(
            {
                "a": {"root": tmp_path, "package": "example"},
                "b": {"root": tmp_path, "package": "example.child"},
            },
            "overlap",
        )
