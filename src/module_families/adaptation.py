"""Explicit, reviewable source projections for unsupported package scaffolding.

This adapter preserves selected declaration text and never infers semantic
equivalence. A review declaration authorizes omitted top-level statements and
empty package initializers. The ordinary compiler checks the resulting static
bindings before output is exposed; arbitrary behavioral equivalence is unproved.
"""

from __future__ import annotations

import ast
import hashlib
import json
import keyword
import re
import tempfile
import tokenize
import tomllib
from io import BytesIO
from pathlib import Path
from typing import Any

from .compiler import CompilationError, compile_project


class AdaptationError(ValueError):
    """A projection is ambiguous, stale, unsupported, or would overwrite work."""


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _names(value: Any, label: str, *, modules: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise AdaptationError(f"{label} must be a list of names")
    if len(set(value)) != len(value):
        raise AdaptationError(f"{label} contains duplicate names")
    for name in value:
        parts = name.split(".") if modules else [name]
        if not all(
            part.isidentifier() and not keyword.iskeyword(part) for part in parts
        ):
            raise AdaptationError(f"{label} contains an invalid name: {name!r}")
    return value


def _bindings(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _start(node: ast.stmt) -> int:
    return min(
        [
            node.lineno,
            *(decorator.lineno for decorator in getattr(node, "decorator_list", [])),
        ]
    )


def _segment(source: str, node: ast.stmt) -> str:
    """AST columns are UTF-8 byte offsets; preserve decorators and statement text."""
    lines = source.splitlines(keepends=True)
    first, last = _start(node), node.end_lineno
    start_column = 0 if first < node.lineno else node.col_offset
    pieces = [line.encode("utf-8") for line in lines[first - 1 : last]]
    if len(pieces) == 1:
        return pieces[0][start_column : node.end_col_offset].decode("utf-8")
    pieces[0] = pieces[0][start_column:]
    pieces[-1] = pieces[-1][: node.end_col_offset]
    return b"".join(pieces).decode("utf-8")


def _module_path(root: Path, module: str) -> Path:
    path = root.joinpath(*module.split("."))
    choices = [
        candidate
        for candidate in (path.with_suffix(".py"), path / "__init__.py")
        if candidate.is_file()
    ]
    if len(choices) != 1:
        raise AdaptationError(
            f"module {module!r} must identify exactly one source file"
        )
    source = choices[0]
    if source != source.resolve() or not source.resolve().is_relative_to(root):
        raise AdaptationError(f"source module cannot use symlinks: {source}")
    return source


def _project_module(
    root: Path, package: str, selection: dict
) -> tuple[Path, bytes, dict]:
    if not isinstance(selection, dict) or set(selection) - {
        "name",
        "sha256",
        "declarations",
        "imports",
    }:
        raise AdaptationError(
            "each modules table supports name, sha256, declarations, and imports"
        )
    names = _names([selection.get("name")], "module name", modules=True)
    module = names[0]
    if module != package and not module.startswith(package + "."):
        raise AdaptationError(f"module {module!r} is outside source.package")
    declarations = set(
        _names(selection.get("declarations", []), f"{module}.declarations")
    )
    imports = set(_names(selection.get("imports", []), f"{module}.imports"))
    if not declarations:
        raise AdaptationError(f"{module}: select at least one declaration")
    path = _module_path(root, module)
    content = path.read_bytes()
    expected_hash = selection.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise AdaptationError(f"{module}.sha256 must pin the reviewed source file")
    if _hash(content) != expected_hash:
        raise AdaptationError(
            f"{module}: source hash differs from the reviewed declaration"
        )
    encoding, _ = tokenize.detect_encoding(BytesIO(content).readline)
    if encoding not in ("utf-8", "utf-8-sig"):
        raise AdaptationError(
            f"{module}: projection currently supports UTF-8 Python source only"
        )
    source = content.decode(encoding)
    tree = ast.parse(source, filename=str(path))
    bound = {}
    imported = {}
    for node in tree.body:
        for name in _bindings(node):
            bound.setdefault(name, []).append(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                imported.setdefault(local, []).append(node)
    for wanted, available, label in (
        (declarations, bound, "declaration"),
        (imports, imported, "import"),
    ):
        for name in sorted(wanted):
            if len(available.get(name, [])) != 1:
                raise AdaptationError(
                    f"{module}: {label} {name!r} must identify exactly one top-level binding"
                )
    selected, omitted, import_changes = [], [], []
    output = ["# Generated by module-families explicit source projection.\n"]
    if tree.body:
        output.append(
            "".join(source.splitlines(keepends=True)[: _start(tree.body[0]) - 1])
        )
    for position, node in enumerate(tree.body):
        segment = _segment(source, node)
        record = {
            "kind": type(node).__name__,
            "start_line": _start(node),
            "end_line": node.end_lineno,
            "bindings": _bindings(node),
            "sha256": _hash(segment.encode("utf-8")),
        }
        keep = bool(declarations & set(_bindings(node)))
        if (
            position == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            keep = True  # Preserve the original module documentation.
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
            aliases = [
                alias
                for alias in node.names
                if future
                or (
                    alias.asname
                    or (
                        alias.name.split(".")[0]
                        if isinstance(node, ast.Import)
                        else alias.name
                    )
                )
                in imports
            ]
            keep = bool(aliases)
            if keep and len(aliases) != len(node.names):
                replacement = (
                    ast.Import(names=aliases)
                    if isinstance(node, ast.Import)
                    else ast.ImportFrom(
                        module=node.module, names=aliases, level=node.level
                    )
                )
                segment = ast.unparse(replacement)
                import_changes.append({**record, "projected_statement": segment})
        if keep:
            output.extend([segment, "\n\n"])
            selected.append(record)
        else:
            omitted.append(record)
    projected = "".join(output).encode("utf-8")
    compile(projected, f"<projection:{module}>", "exec")
    return (
        path.relative_to(root),
        projected,
        {
            "module": module,
            "original_path": path.relative_to(root).as_posix(),
            "original_sha256": _hash(content),
            "projected_sha256": _hash(projected),
            "selected_symbols": [f"{module}:{name}" for name in sorted(declarations)],
            "selected_imports": sorted(imports),
            "selected_top_level": selected,
            "excluded_top_level": omitted,
            "rewritten_imports": import_changes,
        },
    )


def adapt(source_manifest: str | Path, destination: str | Path) -> dict:
    """Project explicitly reviewed bindings, checking compilation before writing.

    The destination is generated output and must be absent, empty, or contain
    exactly this projection. Different existing content is never overwritten.
    Source hash pins make upstream drift require a new review declaration.
    """
    manifest = Path(source_manifest).resolve()
    try:
        manifest_bytes = manifest.read_bytes()
        document = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise AdaptationError(f"cannot read projection manifest: {error}") from error
    if set(document) - {
        "schema_version",
        "name",
        "source",
        "review",
        "modules",
        "external_dependencies",
    }:
        raise AdaptationError("unknown projection manifest fields")
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        raise AdaptationError("projection schema_version must be 1")
    name = document.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise AdaptationError(
            "projection name must contain lowercase letters, digits, and hyphens"
        )
    source = document.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"root", "package"}
        or not isinstance(source["root"], str)
    ):
        raise AdaptationError("source must declare root and package")
    package = _names([source["package"]], "source.package", modules=True)[0]
    root = (manifest.parent / source["root"]).resolve()
    if not root.is_dir():
        raise AdaptationError(f"source.root is not a directory: {root}")
    requested_destination = Path(destination).absolute()
    if requested_destination.is_symlink():
        raise AdaptationError("projection destination cannot be a symlink")
    destination = requested_destination.parent.resolve() / requested_destination.name
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise AdaptationError(
            "projection destination must be separate from the original source tree"
        )
    review = document.get("review")
    if not isinstance(review, dict) or set(review) != {
        "reason",
        "omit_unselected",
        "empty_package_initializers",
    }:
        raise AdaptationError(
            "review must state reason, omit_unselected, and empty_package_initializers"
        )
    if (
        not isinstance(review["reason"], str)
        or not review["reason"].strip()
        or review["omit_unselected"] is not True
        or review["empty_package_initializers"] is not True
    ):
        raise AdaptationError(
            "projection requires explicit review of omissions and empty package initializers"
        )
    modules = document.get("modules")
    if not isinstance(modules, list) or not modules:
        raise AdaptationError("modules must contain explicit module selections")
    external = document.get("external_dependencies", {})
    if not isinstance(external, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in external.items()
    ):
        raise AdaptationError(
            "external_dependencies must map imports to requirement strings"
        )
    files, evidence, module_names = {}, [], set()
    try:
        for selection in modules:
            relative, projected, record = _project_module(root, package, selection)
            if record["module"] in module_names:
                raise AdaptationError(f"duplicate module selection: {record['module']}")
            module_names.add(record["module"])
            files[relative] = projected
            evidence.append(record)
    except (OSError, SyntaxError, UnicodeError) as error:
        raise AdaptationError(f"cannot project source: {error}") from error
    generated = []
    for relative in list(files):
        for parent in relative.parents:
            if parent == Path("."):
                break
            initializer = parent / "__init__.py"
            if initializer not in files:
                files[initializer] = (
                    b"# Explicit projection: original package initializer omitted by review.\n"
                )
                generated.append(initializer.as_posix())
    # Compile a private staging tree, including all generated initializers. No
    # selected candidate is imported or invoked during this check.
    with tempfile.TemporaryDirectory(prefix="mf-projection-check-") as temporary:
        staged = Path(temporary)
        for relative, content in files.items():
            path = staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            compiled = compile_project(staged, package, name, external)
        except CompilationError as error:
            raise AdaptationError(
                f"projected bindings do not compile: {error}"
            ) from error
    report = {
        "schema_version": 1,
        "format": "module-families-source-projection",
        "name": name,
        "manifest_sha256": _hash(manifest_bytes),
        "source_package": package,
        "review": review,
        "modules": sorted(evidence, key=lambda item: item["module"]),
        "generated_initializers": sorted(generated),
        "compiled_cells": len(compiled.cells),
        "checks": [
            "reviewed-source-hashes",
            "selected-definition-text",
            "static-binding-closure",
            "generated-python-syntax",
        ],
        "unproved": [
            "arbitrary behavioral equivalence",
            "omitted initialization effects",
            "external environment compatibility",
        ],
    }
    files[Path("ADAPTATION.json")] = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    ).encode()
    if destination.exists():
        if not destination.is_dir():
            raise AdaptationError("projection destination must be a directory")
        existing = {
            path.relative_to(destination): path
            for path in destination.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if existing and (
            set(existing) != set(files)
            or any(
                path.is_symlink() or path.read_bytes() != files[relative]
                for relative, path in existing.items()
            )
        ):
            raise AdaptationError(
                "projection destination contains different work; choose a fresh destination"
            )
    for relative, content in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
    return report


__all__ = ["AdaptationError", "adapt"]
