from __future__ import annotations

import base64
import csv
import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from module_families.compiler import CompilationError, build_family, compile_project
from module_families.manifest import write_manifest


def project(
    tmp_path: Path, sources: dict[str, str], members: list[dict], **extra
) -> Path:
    root = tmp_path / "source" / "example"
    root.mkdir(parents=True)
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    (tmp_path / "LICENSE").write_text("Test license\n")
    manifest = {
        "schema_version": 1,
        "name": "example",
        "version": "0.1.0",
        "description": "Test family",
        "publisher": "tests",
        "context": {"solves": ["test binding composition"]},
        "source": {
            "root": "source",
            "package": "example",
            "license_files": ["LICENSE"],
        },
        "members": members,
        **extra,
    }
    path = tmp_path / "family.toml"
    write_manifest(manifest, path)
    return path


def card(name: str, symbol: str, **extra) -> dict:
    return {"id": name, "symbol": symbol, "kind": "algorithm", "summary": name, **extra}


def isolated(out: Path, index: dict, code: str, tmp_path: Path) -> str:
    site = tmp_path / "installed"
    site.mkdir()
    for artifact in index["artifacts"]:
        with zipfile.ZipFile(out / artifact["filename"]) as archive:
            archive.extractall(site)
    program = f"import sys, importlib, typing\nsys.path.insert(0, {str(site)!r})\n"
    for member in index["members"]:
        program += f"{member['local_id']} = importlib.import_module({member['import_module']!r}).value\n"
    result = subprocess.run(
        [sys.executable, "-I", "-c", program + code],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_independent_payload_and_shared_record_identity(tmp_path):
    manifest = project(
        tmp_path,
        {
            "__init__.py": "from .unrelated import unused\nfrom .left import make\n",
            "records.py": "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Item:\n    value: int\n",
            "left.py": "from .records import Item\ndef make(value: int) -> Item:\n    return Item(value)\n",
            "right.py": "from .records import Item as Record\ndef accept(value: Record) -> bool:\n    return isinstance(value, Record)\n",
            "unrelated.py": "import impossible_package\ndef unused():\n    return impossible_package.run()\n",
        },
        [card("make", "example:make"), card("accept", "example.right:accept")],
        external_dependencies={"impossible_package": "impossible-package>=1"},
    )
    out = tmp_path / "wheels"
    index = build_family(manifest, out)
    assert len(index["artifacts"]) == 5
    assert all(
        "impossible" not in requirement
        for artifact in index["artifacts"]
        for requirement in artifact["requires_dist"]
    )
    assert all(
        "example/" not in name
        for artifact in index["artifacts"]
        for name in zipfile.ZipFile(out / artifact["filename"]).namelist()
    )
    isolated(
        out,
        index,
        """
item = make(7)
assert accept(item)
assert type(item) is typing.get_type_hints(accept)['value']
assert type(item) is typing.get_type_hints(make)['return']
assert 'example' not in sys.modules
assert 'impossible_package' not in sys.modules
""",
        tmp_path,
    )


def test_mutual_recursion_with_colliding_names_and_shadowed_locals(tmp_path):
    manifest = project(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": "from .b import answer as other\nOFFSET = 1\ndef answer(n: int) -> int:\n    return OFFSET if n == 0 else other(n - 1)\n",
            "b.py": "from .a import answer as other\nOFFSET = 2\ndef answer(n: int) -> int:\n    OFFSET = 3\n    return OFFSET if n == 0 else other(n - 1)\n",
        },
        [card("left", "example.a:answer"), card("right", "example.b:answer")],
    )
    out = tmp_path / "wheels"
    index = build_family(manifest, out)
    isolated(
        out,
        index,
        "assert left(0) == 1\nassert left(1) == 3\nassert right(1) == 1\nassert left.__name__ == 'answer'\n",
        tmp_path,
    )


def test_class_defaults_forward_annotations_and_comprehension_scope(tmp_path):
    manifest = project(
        tmp_path,
        {
            "__init__.py": "",
            "records.py": """from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
OFFSET = 10
def default() -> int:
    return OFFSET
@dataclass
class Item:
    value: int = field(default_factory=default)
    children: tuple["Item", ...] = ()
    def values(self, names: list[int], mode: Literal["default"] = "default") -> list[int]:
        return [default() + OFFSET for OFFSET in names]
""",
        },
        [card("item", "example.records:Item")],
    )
    out = tmp_path / "wheels"
    index = build_family(manifest, out)
    isolated(
        out,
        index,
        "assert item().value == 10\nassert item().values([1, 2]) == [11, 12]\nassert typing.get_type_hints(item)['children'].__args__[0] is item\n",
        tmp_path,
    )


def test_reproducible_wheels_records_and_per_member_version(tmp_path):
    manifest = project(
        tmp_path,
        {"__init__.py": "def answer():\n    return 42\n"},
        [card("answer", "example:answer", version="1.2.0")],
    )
    first = build_family(manifest, tmp_path / "first")
    second = build_family(manifest, tmp_path / "second")
    assert first == second
    assert first["members"][0]["version"] == "1.2.0"
    for artifact in first["artifacts"]:
        data = (tmp_path / "first" / artifact["filename"]).read_bytes()
        assert data == (tmp_path / "second" / artifact["filename"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            record = next(
                name for name in archive.namelist() if name.endswith("/RECORD")
            )
            rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
            assert {row[0] for row in rows} == set(archive.namelist())
            for name, digest, size in rows:
                if name == record:
                    assert digest == size == ""
                    continue
                content = archive.read(name)
                assert int(size) == len(content)
                assert (
                    digest
                    == "sha256="
                    + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                    .rstrip(b"=")
                    .decode()
                )
            metadata = archive.read(
                next(name for name in archive.namelist() if name.endswith("/METADATA"))
            ).decode()
            assert "License-File: LICENSE\n" in metadata
            assert any(
                name.endswith("/licenses/LICENSE") for name in archive.namelist()
            )


def test_unrelated_source_edits_do_not_invalidate_cells(tmp_path):
    root = tmp_path / "example"
    root.mkdir()
    source = root / "__init__.py"
    source.write_text("def answer():\n    return 42\ndef unrelated():\n    return 0\n")
    first = compile_project(tmp_path, "example", "example")
    source.write_text("def answer():\n    return 42\ndef unrelated():\n    return 1\n")
    second = compile_project(tmp_path, "example", "example")
    assert (
        first.symbols["example:answer"]["cell"]
        == second.symbols["example:answer"]["cell"]
    )
    assert (
        first.symbols["example:unrelated"]["cell"]
        != second.symbols["example:unrelated"]["cell"]
    )


def test_class_namespace_fallback_and_chained_bindings(tmp_path):
    manifest = project(
        tmp_path,
        {
            "__init__.py": """value = 9
left = right = object()
class Configuration:
    value = value
    next_value = value + 1
def bindings():
    return left is right
"""
        },
        [
            card("configuration", "example:Configuration"),
            card("bindings", "example:bindings"),
            card("right", "example:right"),
        ],
    )
    out = tmp_path / "wheels"
    index = build_family(manifest, out)
    isolated(
        out,
        index,
        "assert configuration.value == 9\nassert configuration.next_value == 10\nassert bindings()\nassert right is not None\n",
        tmp_path,
    )


@pytest.mark.parametrize(
    "source, message",
    [
        ("from math import *\n", "Wildcard"),
        ("print('side effect')\n", "Unsupported top-level"),
        ("x = 1\nx = 2\n", "Rebound"),
        ("def f():\n    return globals()\n", "Reflective"),
        ("def f():\n    return missing\n", "Unresolved global"),
        ("x = 1\ndef f():\n    global x\n    x += 1\n", "Mutable global"),
        (
            "import importlib\ndef f(name):\n    return importlib.import_module(name)\n",
            "Dynamic import",
        ),
    ],
)
def test_unsupported_source_is_explicit(tmp_path, source, message):
    root = tmp_path / "example"
    root.mkdir()
    (root / "__init__.py").write_text(source)
    with pytest.raises(CompilationError, match=message):
        compile_project(tmp_path, "example", "example")


def test_optional_imports_are_per_definition(tmp_path):
    manifest = project(
        tmp_path,
        {
            "__init__.py": """import importlib
def pure(value):
    return value * 2
def optional(value):
    import impossible_package
    return impossible_package.convert(value)
def dynamic(value):
    return importlib.import_module("another_package").convert(value)
"""
        },
        [
            card("pure", "example:pure"),
            card("optional", "example:optional"),
            card("dynamic", "example:dynamic"),
        ],
        external_dependencies={
            "impossible_package": "impossible-package>=1",
            "another_package": "another-package>=2",
        },
    )
    index = build_family(manifest, tmp_path / "wheels", member_ids=["tests.pure"])
    assert all(
        not requirement.startswith(("impossible", "another"))
        for artifact in index["artifacts"]
        for requirement in artifact["requires_dist"]
    )
    compiled = compile_project(
        tmp_path / "source",
        "example",
        "example",
        {
            "impossible_package": "impossible-package>=1",
            "another_package": "another-package>=2",
        },
    )
    assert compiled.cells[
        compiled.symbols["example:optional"]["cell"]
    ].requires_dist == ("impossible-package>=1",)
    assert compiled.cells[
        compiled.symbols["example:dynamic"]["cell"]
    ].requires_dist == ("another-package>=2",)
