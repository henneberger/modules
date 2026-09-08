"""Compile a constrained Python source tree into shared definition cells.

Source inspection never imports the target package. Internal imports are
resolved through their defining bindings, so eager package facades disappear.
This is a source compiler for explicit Python bindings, not a Python sandbox.
"""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import importlib.util
import json
import keyword
import os
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .wheels import build_wheel, normalize_distribution, wheel_filename

COMPILER_FORMAT = "module-families-cells-v1"
CELL_VERSION = "0.1.0"


class CompilationError(ValueError):
    """A source construct or dependency cannot be preserved explicitly."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass
class _Import:
    module: str
    name: str
    statement: ast.Import | ast.ImportFrom
    target: str | None = None
    root: str | None = None


@dataclass
class _Unit:
    key: str
    module: str
    names: tuple[str, ...]
    node: ast.stmt
    source: str
    dependencies: set[str] = field(default_factory=set)
    eager: set[str] = field(default_factory=set)
    external: set[str] = field(default_factory=set)
    requirements: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class CompiledCell:
    digest: str
    import_module: str
    distribution: str
    source: str
    symbols: tuple[str, ...]
    exports: dict[str, str]
    dependencies: tuple[str, ...]
    requires_dist: tuple[str, ...]


@dataclass
class CompiledProject:
    family: str
    package: str
    cells: dict[str, CompiledCell]
    symbols: dict[str, dict]
    source_digest: str
    relationships: dict[str, dict] = field(default_factory=dict)
    source_files: tuple[str, ...] = ()

    def closure(self, symbols: list[str]) -> list[CompiledCell]:
        """Return dependency-first unique cells for selected source symbols."""
        seen: set[str] = set()
        ordered: list[CompiledCell] = []

        def visit(digest: str) -> None:
            if digest in seen:
                return
            seen.add(digest)
            cell = self.cells[digest]
            for dependency in cell.dependencies:
                visit(dependency)
            ordered.append(cell)

        for symbol in symbols:
            if symbol not in self.symbols:
                raise CompilationError(f"Unknown publication symbol: {symbol}")
            visit(self.symbols[symbol]["cell"])
        return ordered


class _Locals(ast.NodeVisitor):
    """Collect bindings of one lexical scope, skipping nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            alias.asname or alias.name.split(".")[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        # Comprehension targets belong to the comprehension. Assignment
        # expressions bind in the enclosing scope and are collected here.
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr):
                self.visit(child.target)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def _scope(
    statements: list[ast.stmt], args: ast.arguments | None = None
) -> tuple[set[str], set[str]]:
    local = _Locals()
    for statement in statements:
        local.visit(statement)
    if args:
        local.names.update(
            arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        )
        if args.vararg:
            local.names.add(args.vararg.arg)
        if args.kwarg:
            local.names.add(args.kwarg.arg)
    return local.names - local.globals - local.nonlocals, local.globals


class _Names(ast.NodeTransformer):
    """Find/rewrite references to module bindings with lexical scope awareness."""

    def __init__(
        self,
        compiler: _Compiler,
        unit: _Unit,
        *,
        eager: bool = False,
        replacements: dict[str, str] | None = None,
    ) -> None:
        self.compiler = compiler
        self.unit = unit
        self.eager = eager
        self.replacements = replacements or {}
        self.scopes: list[tuple[str, set[str], set[str]]] = []
        self.references: set[str] = set()
        self.external: set[str] = set()
        self.annotation = 0

    def _global(self, name: str) -> bool:
        crossed_function = False
        for kind, names, globals_ in reversed(self.scopes):
            if name in globals_:
                return True
            if kind == "class" and crossed_function:
                continue
            if name in names:
                return False
            crossed_function |= kind in {"function", "comprehension"}
        return True

    def _reference(self, name: str) -> str:
        if not self._global(name):
            return name
        key = self.compiler.resolve(f"{self.unit.module}:{name}", required=False)
        if key in self.compiler.units:
            self.references.add(key)
        elif key in self.compiler.imports:
            self.external.add(key)
        elif name not in vars(builtins) and name not in {
            "__name__",
            "__file__",
            "__package__",
            "__doc__",
            "__annotations__",
        }:
            raise CompilationError(
                f"Unresolved global {name!r} in {self.unit.key} at source line {self.unit.node.lineno}"
            )
        return self.replacements.get(key, name)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        # Store names are rewritten only at module scope or for explicit global
        # statements; local variables with the same spelling remain untouched.
        if isinstance(node.ctx, ast.Load):
            node.id = self._reference(node.id)
        elif not self.scopes:
            key = f"{self.unit.module}:{node.id}"
            self._reference(node.id)
            node.id = self.replacements.get(key, node.id)
        return node

    def _annotation(self, node: ast.AST | None) -> ast.AST | None:
        if node is None or self.eager:
            return node
        self.annotation += 1
        result = self.visit(node)
        self.annotation -= 1
        return result

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if self.annotation and isinstance(node.value, str):
            try:
                expression = ast.parse(node.value, mode="eval")
            except SyntaxError:
                return node
            node.value = ast.unparse(self.visit(expression).body)
        return node

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        literal = (isinstance(node.value, ast.Name) and node.value.id == "Literal") or (
            isinstance(node.value, ast.Attribute) and node.value.attr == "Literal"
        )
        node.value = self.visit(node.value)
        annotation = self.annotation
        if literal:
            self.annotation = 0
        node.slice = self.visit(node.slice)
        self.annotation = annotation
        return node

    def _arguments(self, args: ast.arguments) -> ast.arguments:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            arg.annotation = self._annotation(arg.annotation)
        for arg in (args.vararg, args.kwarg):
            if arg:
                arg.annotation = self._annotation(arg.annotation)
        args.defaults = [self.visit(value) for value in args.defaults]
        args.kw_defaults = [
            self.visit(value) if value else None for value in args.kw_defaults
        ]
        return args

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        is_root = not self.scopes
        original_name = node.name
        node.decorator_list = [self.visit(value) for value in node.decorator_list]
        node.args = self._arguments(node.args)
        node.returns = self._annotation(node.returns)
        if is_root:
            node.name = self.replacements.get(
                f"{self.unit.module}:{original_name}", original_name
            )
        if not self.eager:
            names, globals_ = _scope(node.body, node.args)
            self.scopes.append(("function", names, globals_))
            node.body = [self.visit(value) for value in node.body]
            self.scopes.pop()
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        node.args = self._arguments(node.args)
        if not self.eager:
            names, globals_ = _scope([], node.args)
            self.scopes.append(("function", names, globals_))
            node.body = self.visit(node.body)
            self.scopes.pop()
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        is_root = not self.scopes
        original_name = node.name
        node.decorator_list = [self.visit(value) for value in node.decorator_list]
        node.bases = [self.visit(value) for value in node.bases]
        node.keywords = [self.visit(value) for value in node.keywords]
        if is_root:
            node.name = self.replacements.get(
                f"{self.unit.module}:{original_name}", original_name
            )
        _, globals_ = _scope(node.body)
        # Class suites perform ordered namespace lookup: in `value = value`,
        # the right side can still reference a same-named module binding.
        names: set[str] = set()
        self.scopes.append(("class", names, globals_))
        body = []
        for value in node.body:
            bound, _ = _scope([value])
            body.append(self.visit(value))
            if not (isinstance(value, ast.AnnAssign) and value.value is None):
                names.update(bound)
        node.body = body
        self.scopes.pop()
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        node.target = self.visit(node.target)
        node.annotation = self._annotation(node.annotation)
        if node.value:
            node.value = self.visit(node.value)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        node.targets = [self.visit(value) for value in node.targets]
        # Union and subscript assignments include runtime type aliases with
        # quoted forward references (for example JsonValue's recursive alias).
        alias = isinstance(node.value, (ast.BinOp, ast.Subscript)) and not self.scopes
        self.annotation += int(alias)
        node.value = self.visit(node.value)
        self.annotation -= int(alias)
        return node

    def _comprehension(self, node: ast.AST) -> ast.AST:
        generators = node.generators
        generators[0].iter = self.visit(generators[0].iter)
        names = {
            child.id
            for gen in generators
            for child in ast.walk(gen.target)
            if isinstance(child, ast.Name)
        }
        self.scopes.append(("comprehension", names, set()))
        for index, gen in enumerate(generators):
            if index:
                gen.iter = self.visit(gen.iter)
            gen.ifs = [self.visit(value) for value in gen.ifs]
        if isinstance(node, ast.DictComp):
            node.key, node.value = self.visit(node.key), self.visit(node.value)
        else:
            node.elt = self.visit(node.elt)
        self.scopes.pop()
        return node

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension

    def visit_Global(self, node: ast.Global) -> ast.AST:
        # Shared mutable module state is preserved within a cell only if all
        # writers are known; this initial compiler refuses that open-world case.
        raise CompilationError(
            f"Mutable global declarations are unsupported: {self.unit.key}"
        )

    def visit_Import(self, node: ast.Import) -> ast.AST:
        if self.scopes:
            self.compiler.nested_import(self.unit, node)
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        if self.scopes:
            self.compiler.nested_import(self.unit, node)
        return node


class _Compiler:
    def __init__(
        self,
        source_root: Path,
        package: str,
        family: str,
        external_dependencies: dict,
        dynamic_dependencies: dict,
        sources: dict[str, dict] | None = None,
    ) -> None:
        self.source_root, self.package, self.family = source_root, package, family
        self.sources = sources or {"source": {"root": source_root, "package": package}}
        self.packages = tuple(source["package"] for source in self.sources.values())
        self.external_dependencies = external_dependencies
        self.dynamic_dependencies = dynamic_dependencies
        self.units: dict[str, _Unit] = {}
        self.bindings: dict[str, str] = {}
        self.imports: dict[str, _Import] = {}
        self.modules: set[str] = set()
        self.source_hashes: dict[str, str] = {}

    def internal(self, module: str) -> bool:
        return any(
            module == package or module.startswith(package + ".")
            for package in self.packages
        )

    def requirement(self, root: str) -> set[str]:
        if root in sys.stdlib_module_names or root == "__future__":
            return set()
        if root not in self.external_dependencies:
            raise CompilationError(
                f"Undeclared external import {root!r}; add external_dependencies[{root!r}]"
            )
        value = self.external_dependencies[root]
        return {value} if isinstance(value, str) else set(value)

    def nested_import(self, unit: _Unit, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.ImportFrom):
            if node.level or self.internal(node.module or ""):
                raise CompilationError(
                    f"Function-local internal imports are unsupported: {unit.key}:{node.lineno}"
                )
            roots = [(node.module or "").split(".")[0]]
        else:
            if any(self.internal(alias.name) for alias in node.names):
                raise CompilationError(
                    f"Function-local internal imports are unsupported: {unit.key}:{node.lineno}"
                )
            roots = [alias.name.split(".")[0] for alias in node.names]
        for root in roots:
            unit.requirements.update(self.requirement(root))

    def resolve(self, key: str, *, required: bool = True) -> str:
        seen = set()
        while key in self.bindings and self.bindings[key] != key:
            if key in seen:
                raise CompilationError(f"Cyclic import aliases: {key}")
            seen.add(key)
            key = self.bindings[key]
        if required and key not in self.units and key not in self.imports:
            raise CompilationError(f"Unresolved import or binding: {key}")
        return key

    def _import(
        self, module: str, package: str, node: ast.Import | ast.ImportFrom
    ) -> None:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            return
        for alias in node.names:
            if alias.name == "*":
                raise CompilationError(
                    f"Wildcard imports are unsupported: {module}:{node.lineno}"
                )
            local = alias.asname or (
                alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
            )
            key = f"{module}:{local}"
            if key in self.bindings:
                raise CompilationError(f"Rebound top-level name: {key}")
            if isinstance(node, ast.ImportFrom):
                name = (
                    importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), package
                    )
                    if node.level
                    else (node.module or "")
                )
                internal = self.internal(name)
                if internal:
                    self.bindings[key] = f"{name}:{alias.name}"
                    continue
                root = name.split(".")[0]
                statement = ast.ImportFrom(
                    module=node.module, names=[copy.deepcopy(alias)], level=0
                )
            else:
                if self.internal(alias.name):
                    raise CompilationError(
                        f"Module-object internal imports are unsupported; use explicit from imports: {module}:{node.lineno}"
                    )
                root = alias.name.split(".")[0]
                statement = ast.Import(names=[copy.deepcopy(alias)])
            self.bindings[key] = key
            self.imports[key] = _Import(module, local, statement, root=root)

    def read(self) -> None:
        for source in self.sources.values():
            self._read_source(
                Path(source["root"]), source["package"], source.get("modules")
            )

    def _read_source(
        self, source_root: Path, source_package: str, modules: list[str] | None = None
    ) -> None:
        package_root = source_root.joinpath(*source_package.split("."))
        if not package_root.is_dir():
            raise CompilationError(f"Package directory does not exist: {package_root}")
        for path in _source_paths(source_root, source_package, modules):
            relative = path.relative_to(source_root).with_suffix("")
            parts = relative.parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            package = (
                module if path.name == "__init__.py" else module.rpartition(".")[0]
            )
            if module in self.modules:
                raise CompilationError(
                    f"Source packages define the same module: {module}"
                )
            self.modules.add(module)
            source = path.read_bytes()
            self.source_hashes[str(path.relative_to(source_root))] = hashlib.sha256(
                source
            ).hexdigest()
            tree = ast.parse(source, filename=str(path))
            for node in tree.body:
                if (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    continue
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self._import(module, package, node)
                    continue
                if (
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Name)
                    and node.test.id == "TYPE_CHECKING"
                    and not node.orelse
                    and all(
                        isinstance(item, (ast.Import, ast.ImportFrom))
                        for item in node.body
                    )
                ):
                    for item in node.body:
                        self._import(module, package, item)
                    continue
                if (
                    isinstance(node, ast.AugAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "__all__"
                ):
                    continue
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    names = [node.name]
                    if getattr(node, "type_params", []):
                        raise CompilationError(
                            f"PEP 695 type parameters are not supported yet: {module}:{node.lineno}"
                        )
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    if not all(isinstance(target, ast.Name) for target in targets):
                        raise CompilationError(
                            f"Destructuring or attribute top-level assignment is unsupported: {module}:{node.lineno}"
                        )
                    names = [target.id for target in targets]
                    if names == ["__all__"]:
                        continue
                    if isinstance(node, ast.AnnAssign) and node.value is None:
                        raise CompilationError(
                            f"Unbound annotated global is unsupported: {module}:{node.lineno}"
                        )
                else:
                    raise CompilationError(
                        f"Unsupported top-level {type(node).__name__}: {module}:{node.lineno}"
                    )
                key = f"{module}:{names[0]}"
                for name in names:
                    qualified = f"{module}:{name}"
                    if qualified in self.bindings:
                        raise CompilationError(f"Rebound top-level name: {qualified}")
                    self.bindings[qualified] = key
                self.units[key] = _Unit(
                    key, module, tuple(names), node, ast.unparse(node)
                )

    def analyze(self) -> None:
        for unit in self.units.values():
            scan = _Names(self, unit)
            scan.visit(copy.deepcopy(unit.node))
            unit.dependencies = scan.references - {unit.key}
            unit.external = scan.external
            eager = _Names(self, unit, eager=True)
            eager.visit(copy.deepcopy(unit.node))
            unit.eager = eager.references - {unit.key}
            for key in unit.external:
                unit.requirements.update(self.requirement(self.imports[key].root or ""))
            explicit = self.dynamic_dependencies.get(unit.key, ())
            unit.requirements.update(
                [explicit] if isinstance(explicit, str) else explicit
            )
            for node in ast.walk(unit.node):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id in {
                    "exec",
                    "eval",
                    "globals",
                    "locals",
                    "__import__",
                }:
                    raise CompilationError(
                        f"Reflective execution is unsupported: {unit.key}:{node.lineno}"
                    )
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                ):
                    if (
                        node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        unit.requirements.update(
                            self.requirement(node.args[0].value.split(".")[0])
                        )
                    elif unit.key not in self.dynamic_dependencies:
                        raise CompilationError(
                            f"Dynamic import requires an explicit declaration (possibly [] for an audited helper): {unit.key}"
                        )

    def components(self) -> list[list[str]]:
        # Tarjan's algorithm; the source graph is shallow enough for this
        # recursive walk, while emission below works in dependency order.
        index = 0
        indices: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        active: set[str] = set()
        components: list[list[str]] = []

        def visit(key: str) -> None:
            nonlocal index
            indices[key] = low[key] = index
            index += 1
            stack.append(key)
            active.add(key)
            for dependency in sorted(self.units[key].dependencies):
                if dependency not in indices:
                    visit(dependency)
                    low[key] = min(low[key], low[dependency])
                elif dependency in active:
                    low[key] = min(low[key], indices[dependency])
            if low[key] == indices[key]:
                component = []
                while True:
                    member = stack.pop()
                    active.remove(member)
                    component.append(member)
                    if member == key:
                        break
                components.append(sorted(component))

        for key in sorted(self.units):
            if key not in indices:
                visit(key)
        return components

    def emit(self) -> CompiledProject:
        cells: dict[str, CompiledCell] = {}
        symbols: dict[str, dict] = {}
        for component in self.components():
            members = set(component)
            dependencies = sorted(
                {
                    dependency
                    for key in component
                    for dependency in self.units[key].dependencies
                    if dependency not in members
                }
            )
            externals = sorted(
                {external for key in component for external in self.units[key].external}
            )
            own_bindings = [
                f"{self.units[key].module}:{name}"
                for key in component
                for name in self.units[key].names
            ]
            names = sorted(set([*own_bindings, *dependencies, *externals]))
            counts = Counter(key.rpartition(":")[2] for key in names)
            replacements = {
                key: key.rpartition(":")[2]
                if counts[key.rpartition(":")[2]] == 1
                else "_mf_"
                + hashlib.sha256(key.encode()).hexdigest()[:12]
                + "_"
                + key.rpartition(":")[2]
                for key in names
            }
            order: list[str] = []
            visiting: set[str] = set()
            ordered: set[str] = set()

            def initialize(
                key: str,
                ordered: set[str] = ordered,
                visiting: set[str] = visiting,
                members: set[str] = members,
                order: list[str] = order,
            ) -> None:
                if key in ordered:
                    return
                if key in visiting:
                    raise CompilationError(
                        f"Initialization cycle cannot be preserved: {key}"
                    )
                visiting.add(key)
                for dependency in sorted(self.units[key].eager & members):
                    initialize(dependency)
                visiting.remove(key)
                ordered.add(key)
                order.append(key)

            for key in component:
                initialize(key)
            dependency_digests = sorted({symbols[key]["cell"] for key in dependencies})
            requirements = sorted(
                {value for key in component for value in self.units[key].requirements}
            )
            identity = {
                "format": COMPILER_FORMAT,
                "family": self.family,
                "statements": [[key, self.units[key].source] for key in order],
                "dependencies": [
                    [key, symbols[key]["cell"], symbols[key]["export"]]
                    for key in dependencies
                ],
                "external_imports": [
                    [key, ast.unparse(self.imports[key].statement)] for key in externals
                ],
                "requires_dist": requirements,
            }
            digest = _digest(identity)
            lines = [
                f'"""Shared source cell {digest}. Generated from explicit Python bindings."""',
                "from __future__ import annotations",
                "",
            ]
            for key in externals:
                statement = copy.deepcopy(self.imports[key].statement)
                alias = statement.names[0]
                replacement = replacements[key]
                if (
                    isinstance(statement, ast.Import)
                    and not alias.asname
                    and "." in alias.name
                ):
                    # `import a.b` binds a, whereas `import a.b as x` binds b.
                    # Preserve both loading the child and binding its root.
                    lines.append(ast.unparse(statement))
                    lines.append(f"import {alias.name.split('.')[0]} as {replacement}")
                else:
                    alias.asname = replacement
                    lines.append(ast.unparse(statement))
            for key in dependencies:
                target = symbols[key]
                lines.append(
                    f"from {cells[target['cell']].import_module} import {target['export']} as {replacements[key]}"
                )
            lines.append("")
            for key in order:
                transformed = _Names(
                    self, self.units[key], replacements=replacements
                ).visit(copy.deepcopy(self.units[key].node))
                ast.fix_missing_locations(transformed)
                lines.extend([f"# Source: {key}", ast.unparse(transformed), ""])
                original = self.units[key].node
                if (
                    isinstance(
                        original, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    and replacements[key] != original.name
                ):
                    lines.extend(
                        [
                            f"{replacements[key]}.__name__ = {original.name!r}",
                            f"{replacements[key]}.__qualname__ = {original.name!r}",
                            "",
                        ]
                    )
            generated = "\n".join(lines)
            try:
                compile(generated, f"mf_cells/c_{digest}.py", "exec")
            except (SyntaxError, ValueError) as error:
                raise CompilationError(
                    f"Invalid generated cell for {component}: {error}"
                ) from error
            exports = {key: replacements[key] for key in own_bindings}
            cell = CompiledCell(
                digest,
                f"mf_cells.c_{digest}",
                f"mf-cell-{digest}",
                generated,
                tuple(own_bindings),
                exports,
                tuple(dependency_digests),
                tuple(requirements),
            )
            cells[digest] = cell
            for key in own_bindings:
                unit = self.units[self.resolve(key)]
                kind = (
                    "class"
                    if isinstance(unit.node, ast.ClassDef)
                    else "function"
                    if isinstance(unit.node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else "binding"
                )
                symbols[key] = {
                    "cell": digest,
                    "export": replacements[key],
                    "kind": kind,
                    "source_line": unit.node.lineno,
                    "docstring": ast.get_docstring(unit.node)
                    if kind != "binding"
                    else None,
                }
        # Resolve public facade aliases for callers that explicitly name them.
        for key in sorted(self.bindings):
            resolved = self.resolve(key, required=False)
            if key not in symbols and resolved in symbols:
                symbols[key] = dict(symbols[resolved])
        relationships = {
            key: {
                "symbol": key,
                "source_line": unit.node.lineno,
                "dependencies": sorted(unit.dependencies),
                "initialization_dependencies": sorted(unit.eager),
                "external_imports": [
                    {
                        "binding": binding,
                        "statement": ast.unparse(self.imports[binding].statement),
                    }
                    for binding in sorted(unit.external)
                ],
                "external_requirements": sorted(unit.requirements),
            }
            for key, unit in sorted(self.units.items())
        }
        return CompiledProject(
            self.family,
            self.package,
            cells,
            symbols,
            _digest(self.source_hashes),
            relationships,
            tuple(sorted(self.source_hashes)),
        )


def compile_project(
    source_root: Path,
    package: str,
    family: str,
    external_dependencies: dict | None = None,
    dynamic_dependencies: dict | None = None,
    *,
    cache_dir: Path | None = None,
    stats: dict | None = None,
) -> CompiledProject:
    """Statically compile a package rooted beneath ``source_root``.

    Dependency mappings name import roots and original ``module:symbol``
    identities respectively. Values are PEP 508 strings or lists of strings.
    """
    return compile_sources(
        {"source": {"root": Path(source_root), "package": package}},
        family,
        external_dependencies,
        dynamic_dependencies,
        cache_dir=cache_dir,
        stats=stats,
    )


def _cache_project(document: dict, key: str) -> CompiledProject:
    """Decode a local JSON analysis cache without executing its contents."""
    if not isinstance(document, dict):
        raise ValueError("Analysis cache must be an object")
    if document.get("key") != key or document.get("schema_version") != 1:
        raise ValueError("Analysis cache identity mismatch")
    payload = document["project"]
    if document.get("sha256") != _digest(payload):
        raise ValueError("Analysis cache payload is corrupt")
    cells = {}
    for digest, data in payload["cells"].items():
        data = dict(data)
        for field_name in ("symbols", "dependencies", "requires_dist"):
            data[field_name] = tuple(data[field_name])
        cell = CompiledCell(**data)
        if digest != cell.digest or cell.import_module != f"mf_cells.c_{digest}":
            raise ValueError("Invalid cached cell identity")
        cells[digest] = cell
    for cell in cells.values():
        if any(dependency not in cells for dependency in cell.dependencies):
            raise ValueError("Incomplete cached dependency graph")
    for symbol in payload["symbols"].values():
        if symbol["cell"] not in cells:
            raise ValueError("Invalid cached source binding")
    return CompiledProject(
        payload["family"],
        payload["package"],
        cells,
        payload["symbols"],
        payload["source_digest"],
        payload["relationships"],
        tuple(payload["source_files"]),
    )


def _source_paths(
    root: Path, package: str, modules: list[str] | None = None
) -> list[Path]:
    """Resolve an explicit module scope without importing package facades."""
    if modules is None:
        return sorted(root.joinpath(*package.split(".")).rglob("*.py"))
    if (
        not isinstance(modules, list)
        or not modules
        or any(not isinstance(module, str) for module in modules)
    ):
        raise CompilationError("source.modules must be a nonempty list of module names")
    if len(set(modules)) != len(modules):
        raise CompilationError("source.modules contains duplicate module names")
    paths = []
    for module in sorted(modules):
        if not (module == package or module.startswith(package + ".")) or not all(
            part.isidentifier() and not keyword.iskeyword(part)
            for part in module.split(".")
        ):
            raise CompilationError(
                f"Selected module is outside source package {package!r}: {module!r}"
            )
        stem = root.joinpath(*module.split("."))
        candidates = [
            path
            for path in (stem.with_suffix(".py"), stem / "__init__.py")
            if path.is_file()
        ]
        if len(candidates) != 1:
            raise CompilationError(
                f"Selected module {module!r} must resolve to exactly one Python source file"
            )
        paths.extend(candidates)
    return sorted(paths)


def compile_sources(
    sources: dict[str, dict],
    family: str,
    external_dependencies: dict | None = None,
    dynamic_dependencies: dict | None = None,
    *,
    cache_dir: Path | None = None,
    stats: dict | None = None,
) -> CompiledProject:
    """Compile registered source packages together, including cross-root edges.

    Source roots here are filesystem paths. The optional JSON cache is local
    build state, not a trusted artifact exchange protocol. Every lookup hashes
    the current source bytes, compiler implementation and dependency mappings.
    A changed input invalidates the complete analysis graph.
    """
    if not sources:
        raise CompilationError("At least one source package is required")
    sources = {
        alias: {**source, "root": Path(source["root"]).resolve()}
        for alias, source in sorted(sources.items())
    }
    packages = [source["package"] for source in sources.values()]
    for package in packages:
        if not isinstance(package, str) or not all(
            part.isidentifier() and not keyword.iskeyword(part)
            for part in package.split(".")
        ):
            raise CompilationError(f"Invalid source package: {package!r}")
    for index, package in enumerate(packages):
        if any(
            package == other
            or package.startswith(other + ".")
            or other.startswith(package + ".")
            for other in packages[:index]
        ):
            raise CompilationError(
                f"Duplicate or overlapping source package: {package}"
            )
    external = external_dependencies or {}
    dynamic = dynamic_dependencies or {}
    observations = {}
    for source in sources.values():
        root = source["root"]
        package_root = root.joinpath(*source["package"].split("."))
        if not package_root.is_dir():
            raise CompilationError(f"Package directory does not exist: {package_root}")
        for path in _source_paths(root, source["package"], source.get("modules")):
            if path.is_symlink() or not path.resolve().is_relative_to(
                package_root.resolve()
            ):
                raise CompilationError(f"Source paths cannot use symlinks: {path}")
            observations[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    key = _digest(
        {
            "format": COMPILER_FORMAT,
            "compiler": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": [sys.implementation.name, list(sys.version_info)],
            "family": family,
            "packages": packages,
            "module_scopes": {
                source["package"]: source.get("modules") for source in sources.values()
            },
            "source_files": observations,
            "external_dependencies": external,
            "dynamic_dependencies": dynamic,
        }
    )
    if stats is not None:
        stats.update(
            {
                "analysis_cache": "disabled" if cache_dir is None else "miss",
                "analysis_key": key,
                "source_files_hashed": len(observations),
            }
        )
    cache_path = None if cache_dir is None else Path(cache_dir) / f"analysis-{key}.json"
    if cache_path is not None and cache_path.is_file() and not cache_path.is_symlink():
        try:
            project = _cache_project(json.loads(cache_path.read_text()), key)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            if stats is not None:
                stats["analysis_cache"] = "corrupt"
        else:
            if stats is not None:
                stats["analysis_cache"] = "hit"
            return project
    first = next(iter(sources.values()))
    compiler = _Compiler(
        first["root"], first["package"], family, external, dynamic, sources
    )
    compiler.read()
    compiler.analyze()
    project = compiler.emit()
    # If inputs changed between hashing and parsing, do not associate the
    # result with the earlier snapshot. The next invocation will reanalyze it.
    if cache_path is not None and project.source_digest == _digest(observations):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.is_symlink():
            raise CompilationError(
                f"Analysis cache must not be a symlink: {cache_path}"
            )
        payload = asdict(project)
        content = (
            _json(
                {
                    "schema_version": 1,
                    "key": key,
                    "sha256": _digest(payload),
                    "project": payload,
                }
            )
            + "\n"
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=cache_path.parent,
                prefix=".analysis-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            os.replace(temporary, cache_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return project


@dataclass
class _PreparedBuild:
    manifest: dict
    project: CompiledProject
    cards: list[dict]
    cells: list[CompiledCell]
    licenses: dict[str, bytes]


def _prepare_build(
    manifest_path: Path,
    member_ids: list[str] | None,
    manifest: dict | None = None,
    *,
    cache_dir: Path | None = None,
    stats: dict | None = None,
) -> _PreparedBuild:
    # Kept here so the raw source compiler has no manifest-format dependency.
    from .catalog import validate_manifest
    from .manifest import load_manifest

    if manifest is None:
        manifest = load_manifest(manifest_path)
    validate_manifest(manifest)
    family = manifest["name"]
    version = manifest["version"]
    sources = manifest.get("sources", {})
    if manifest.get("source"):
        sources = {"source": manifest["source"]}
    if sources:
        project = compile_sources(
            {
                alias: {
                    **source,
                    "root": (manifest_path.parent / source["root"]).resolve(),
                }
                for alias, source in sources.items()
            },
            family,
            manifest.get("external_dependencies", {}),
            manifest.get("dynamic_dependencies", {}),
            cache_dir=cache_dir,
            stats=stats,
        )
    else:
        project = CompiledProject(family, "", {}, {}, _digest({}))
        if stats is not None:
            stats.update({"analysis_cache": "not-applicable", "source_files_hashed": 0})
    cards = manifest["members"]
    if len({card["id"] for card in cards}) != len(cards):
        raise CompilationError("Duplicate family member IDs")
    if member_ids is not None:
        selected = set(member_ids)
        missing = selected - {card["id"] for card in cards}
        if missing:
            raise CompilationError(f"Unknown member IDs: {sorted(missing)}")
        cards = [card for card in cards if card["id"] in selected]
    cards = sorted(cards, key=lambda card: card["id"])
    licenses: dict[str, bytes] = {}
    for alias, source in sorted(sources.items()):
        for filename in source.get("license_files", []):
            path = manifest_path.parent / filename
            basename = path.name if len(sources) == 1 else f"{alias}-{path.name}"
            if basename in licenses:
                raise CompilationError(f"Duplicate license basename: {basename}")
            licenses[basename] = path.read_bytes()
    cells = project.closure(
        [symbol for card in cards for symbol in _member_exports(card).values()]
    )
    distributions = set()
    for card in cards:
        for export, symbol in _member_exports(card).items():
            if not export.isidentifier() or keyword.iskeyword(export):
                raise CompilationError(f"Invalid export name {export!r}: {symbol}")
        distribution = normalize_distribution(f"mf-{family}-{card['id']}")
        if distribution in distributions:
            raise CompilationError(
                f"Member IDs normalize to the same distribution: {distribution}"
            )
        distributions.add(distribution)
        wheel_filename(distribution, card.get("version", version))
    return _PreparedBuild(manifest, project, cards, cells, licenses)


def _member_exports(card: dict) -> dict[str, str]:
    if card.get("kind") == "module":
        exports = card.get("exports")
        if not isinstance(exports, dict) or not exports:
            raise CompilationError(
                f"Grouped module {card['id']!r} requires nonempty exports"
            )
        return dict(sorted(exports.items()))
    symbol = card["symbol"]
    return {symbol.rpartition(":")[2]: symbol}


_PLAN_LIMITATIONS = [
    "Analysis covers each entire source package unless source.modules explicitly scopes it; an optional coarse JSON cache reuses unchanged analysis, while member selection limits emitted artifacts.",
    "Unsupported source in the declared module scope blocks the plan; errors report the first blocking issue, not a complete diagnostic inventory. Excluded internal dependencies must be added explicitly.",
    "External requirements are declarations and remain unresolved; this is not an environment lock or a proof of effects or semantic equivalence.",
    "Each build writes one index for its selected subset; use separate output directories to retain multiple local publication indexes.",
]


def plan_build(
    manifest_path: Path,
    member_ids: list[str] | None = None,
    *,
    cache_dir: Path | None = None,
    stats: dict | None = None,
) -> dict:
    """Describe exact selected source closures without importing or writing them.

    Uses the same preparation, dependency graph, SCC cells, and validation as
    ``build_family``. A blocked plan is data with the first actionable error.
    """
    from .manifest import load_manifest

    path = Path(manifest_path)
    plan: dict = {
        "schema_version": 1,
        "format": "module-families-build-plan",
        "status": "blocked",
        "family": None,
        "selection": {
            "mode": "all" if member_ids is None else "explicit",
            "requested": None if member_ids is None else sorted(set(member_ids)),
        },
        "counts": {},
        "members": [],
        "cells": [],
        "source_relationships": [],
        "external_requirements": [],
        "errors": [],
        "limitations": list(_PLAN_LIMITATIONS),
    }
    try:
        manifest = load_manifest(path)
        plan["family"] = {
            key: manifest.get(key, {} if key == "context" else "")
            for key in ("name", "version", "description", "context")
        }
        plan["counts"]["public_inventory"] = len(manifest["members"])
        prepared = _prepare_build(
            path, member_ids, manifest, cache_dir=cache_dir, stats=stats
        )
    except (ValueError, OSError, SyntaxError, KeyError, TypeError) as error:
        plan["errors"].append({"type": type(error).__name__, "message": str(error)})
        return plan
    project, cards, cells = prepared.project, prepared.cards, prepared.cells
    users: dict[str, list[str]] = {cell.digest: [] for cell in cells}
    roots = set()
    for card in cards:
        exports = _member_exports(card)
        closure = project.closure(list(exports.values()))
        member_roots = sorted(
            {project.symbols[symbol]["cell"] for symbol in exports.values()}
        )
        roots.update(member_roots)
        for cell in closure:
            users[cell.digest].append(card["id"])
        plan["members"].append(
            {
                "id": card["id"],
                **(
                    {"exports": exports}
                    if card.get("kind") == "module"
                    else {"symbol": card["symbol"]}
                ),
                "version": card.get("version", manifest["version"]),
                "distribution": normalize_distribution(
                    f"mf-{manifest['name']}-{card['id']}"
                ),
                "root_cell": member_roots[0] if len(member_roots) == 1 else None,
                "root_cells": member_roots,
                "cells": [cell.digest for cell in closure],
                "support_cells": len(closure) - len(member_roots),
                "external_requirements": sorted(
                    {item for cell in closure for item in cell.requires_dist}
                ),
            }
        )
    selected_symbols = {symbol for cell in cells for symbol in cell.symbols}
    plan["source_relationships"] = [
        relationship
        for symbol, relationship in project.relationships.items()
        if symbol in selected_symbols
    ]
    plan["cells"] = [
        {
            "digest": cell.digest,
            "distribution": cell.distribution,
            "symbols": list(cell.symbols),
            "dependencies": list(cell.dependencies),
            "external_requirements": list(cell.requires_dist),
            "generated_source_bytes": len(cell.source.encode()),
            "used_by": users[cell.digest],
        }
        for cell in cells
    ]
    plan["external_requirements"] = sorted(
        {item for cell in cells for item in cell.requires_dist}
    )
    plan["counts"].update(
        {
            "source_files": len(project.source_files),
            "source_definitions": len(project.relationships),
            "compiled_cells": len(project.cells),
            "selected_members": len(cards),
            "reachable_cells": len(cells),
            "root_cells": len(roots),
            "support_cells": len(cells) - len(roots),
            "shared_cells": sum(len(members) > 1 for members in users.values()),
            "selected_source_bindings": len(selected_symbols),
            "omitted_cells": len(project.cells) - len(cells),
            "wheel_artifacts": len(cells) + len(cards),
            "generated_source_bytes": sum(len(cell.source.encode()) for cell in cells),
        }
    )
    plan["status"] = "ready"
    if manifest.get("interfaces"):
        plan["interfaces"] = manifest["interfaces"]
    return plan


def build_family(
    manifest_path: Path,
    out: Path,
    member_ids: list[str] | None = None,
    *,
    cache_dir: Path | None = None,
    stats: dict | None = None,
) -> dict:
    """Build selected independent member wheels and their exact shared closure."""
    manifest_path, out = Path(manifest_path), Path(out)
    build_stats: dict = {} if stats is None else stats
    prepared = _prepare_build(
        manifest_path,
        member_ids,
        cache_dir=out / ".cache" if cache_dir is None else Path(cache_dir),
        stats=build_stats,
    )
    manifest, project, cards, licenses = (
        prepared.manifest,
        prepared.project,
        prepared.cards,
        prepared.licenses,
    )
    family, version = manifest["name"], manifest["version"]
    artifacts = []
    for cell in prepared.cells:
        dependency_names = [
            project.cells[digest].distribution for digest in cell.dependencies
        ]
        requirements = [
            *cell.requires_dist,
            *(f"{name}=={CELL_VERSION}" for name in dependency_names),
        ]
        provenance = {
            "schema_version": 1,
            "compiler": COMPILER_FORMAT,
            "cell": cell.digest,
            "source_symbols": cell.symbols,
        }
        artifact = build_wheel(
            out,
            distribution=cell.distribution,
            version=CELL_VERSION,
            files={cell.import_module.replace(".", "/") + ".py": cell.source},
            requires_dist=requirements,
            license_files=licenses,
            description="Shared Python definitions: " + ", ".join(cell.symbols),
            provenance=_json(provenance),
        )
        artifact["dependencies"] = sorted(dependency_names)
        artifacts.append(artifact)
    members = []
    for card in cards:
        exports = _member_exports(card)
        targets = {name: project.symbols[symbol] for name, symbol in exports.items()}
        root_cells = {
            target["cell"]: project.cells[target["cell"]] for target in targets.values()
        }
        grouped = card.get("kind") == "module"
        symbol = next(iter(exports.values()))
        target = project.symbols[symbol]
        cell = project.cells[target["cell"]]
        export = "value" if grouped else next(iter(exports))
        distribution = normalize_distribution(f"mf-{family}-{card['id']}")
        member_version = card.get("version", version)
        facade_digest = _digest(
            {
                "compiler": COMPILER_FORMAT,
                "family": family,
                "version": member_version,
                "member": card,
                **(
                    {
                        "exports": {
                            name: {"cell": item["cell"], "export": item["export"]}
                            for name, item in targets.items()
                        }
                    }
                    if grouped
                    else {"cell": cell.digest, "export": target["export"]}
                ),
            }
        )
        import_module = f"mf_members.m_{facade_digest}"
        if grouped:
            lines = [repr(card.get("summary", ""))]
            for position, item in enumerate(targets.values()):
                lines.append(
                    f"from {project.cells[item['cell']].import_module} import {item['export']} as _mf_export_{position}"
                )
            lines.append(
                "value = {"
                + ", ".join(
                    f"{name!r}: _mf_export_{position}"
                    for position, name in enumerate(targets)
                )
                + "}"
            )
            attributes = [name for name in targets if name not in {"value", "__all__"}]
            lines.extend(f"{name} = value[{name!r}]" for name in attributes)
            lines.append(f"__all__ = {['value', *attributes]!r}")
            facade = "\n".join(lines) + "\n"
        else:
            facade = f"{card.get('summary', '')!r}\nfrom {cell.import_module} import {target['export']} as {export}\nvalue = {export}\n__all__ = [{export!r}, 'value']\n"
        provenance = {
            "schema_version": 1,
            "compiler": COMPILER_FORMAT,
            **(
                {"source_cells": sorted(root_cells), "source_exports": exports}
                if grouped
                else {"source_cell": cell.digest, "source_symbol": symbol}
            ),
            "family": {
                key: manifest.get(key)
                for key in ("name", "version", "description", "context")
            },
            "member": card,
        }
        artifact = build_wheel(
            out,
            distribution=distribution,
            version=member_version,
            files={import_module.replace(".", "/") + ".py": facade},
            requires_dist=[
                f"{item.distribution}=={CELL_VERSION}" for item in root_cells.values()
            ],
            license_files=licenses,
            description=card.get("summary", ""),
            provenance=_json(provenance),
        )
        artifact["dependencies"] = sorted(
            item.distribution for item in root_cells.values()
        )
        artifacts.append(artifact)
        members.append(
            {
                **card,
                "version": member_version,
                "distribution": distribution,
                "wheel": artifact["filename"],
                "sha256": artifact["sha256"],
                "import_module": import_module,
                "export": export,
            }
        )
    index = {
        "schema_version": 1,
        "publisher": manifest["publisher"],
        "family": {
            key: manifest.get(key, {} if key == "context" else "")
            for key in ("name", "version", "description", "context")
        },
        "members": members,
        "artifacts": sorted(artifacts, key=lambda item: item["distribution"]),
        "build": {
            "compiler": COMPILER_FORMAT,
            "source_digest": project.source_digest,
            "compiled_cells": len(project.cells),
            "selected_cells": len(artifacts) - len(members),
        },
    }
    if manifest.get("interfaces"):
        index["interfaces"] = manifest["interfaces"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    (out / "build-stats.json").write_text(
        json.dumps(build_stats, indent=2, sort_keys=True) + "\n"
    )
    return index
