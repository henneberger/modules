"""A separately checked, substructural composition language lowering to Python.

.mfl uses a deliberately small Python-shaped grammar. It is not arbitrary Python:
only bindings, declared module calls, moves, affine discard, terminal branches,
and returns are accepted. The checker accounts for resources on normal paths.
Published Python operation implementations remain a declared trust boundary.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import tomllib
from pathlib import Path

from . import __version__
from .compiler import build_family
from .interfaces import (
    signature_from_spec,
    validate_interface,
    validate_interface_reference,
)
from .manifest import write_manifest
from .module_build import _name, _shape
from .registry import canonical_bytes

FORMAT = "module-families-checked-program-1"


class TypeCheckError(ValueError):
    """A checked program violates its grammar, types, usage, or effect contract."""


def read_program(source):
    if isinstance(source, dict):
        raise TypeCheckError(
            "program authoring requires a TOML file and a relative .mfl source"
        )
    path = Path(source).resolve()
    if path.suffix != ".toml":
        raise TypeCheckError("program manifest must be TOML")
    doc = tomllib.loads(path.read_text())
    _shape(
        doc,
        {
            "schema_version",
            "family",
            "publisher",
            "module",
            "ports",
            "program",
            "policy",
            "associated",
            "instance_sharing",
            "instance_exports",
        },
        "checked program",
    )
    if type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise TypeCheckError("program schema_version must be 1")
    _shape(doc.get("family"), {"name", "version", "description", "context"}, "family")
    _shape(doc.get("publisher"), {"name"}, "publisher")
    _shape(
        doc.get("module"),
        {"id", "version", "provides", "summary", "capabilities"},
        "module",
    )
    _shape(doc.get("program"), {"source", "export"}, "program")
    _name(doc["module"].get("id"))
    validate_interface_reference(doc["module"].get("provides"))
    for owner, fields in (
        (doc["family"], ("name", "version", "description")),
        (doc["publisher"], ("name",)),
        (doc["module"], ("version",)),
    ):
        if any(
            not isinstance(owner.get(key), str) or not owner[key].strip()
            for key in fields
        ):
            raise TypeCheckError(f"missing required nonempty field: {fields}")
    ports = doc.setdefault("ports", {})
    if not isinstance(ports, dict):
        raise TypeCheckError("ports must be a table")
    for name, port in ports.items():
        _name(name)
        if name.startswith("_") or name == "discard":
            raise TypeCheckError(f"reserved module port name: {name}")
        _shape(port, {"requires"}, "typed port")
        validate_interface_reference(port.get("requires"))
    policy = doc.setdefault("policy", {})
    _shape(policy, {"allowed_effects"}, "policy")
    for values in (
        policy.get("allowed_effects", []),
        doc["module"].get("capabilities", []),
    ):
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v for v in values
        ):
            raise TypeCheckError(
                "effects and capabilities must be lists of nonempty strings"
            )
    filename = doc["program"].get("source")
    if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
        raise TypeCheckError("program.source must be a relative .mfl path")
    program_path = (path.parent / filename).resolve()
    if not program_path.is_relative_to(path.parent) or program_path.suffix != ".mfl":
        raise TypeCheckError(
            "program source must be a .mfl file within the manifest directory"
        )
    text = program_path.read_text()
    if len(text.encode()) > 1_000_000:
        raise TypeCheckError("checked source exceeds 1 MB")
    canonical_bytes(doc)
    return doc, text


def _resolved_types(spec):
    typing = spec.get("typing")
    if typing is None:
        raise TypeCheckError(
            f"interface {spec['id']}@{spec['version']} has no typed contract"
        )
    return typing["types"]


class _Checker:
    def __init__(self, doc, source, interfaces, imports=None, result=None):
        self.doc, self.source, self.interfaces = doc, source, interfaces
        self.types, self.effects = {}, set()
        self.imports = {}
        self.boundary = []
        for spec in interfaces:
            for value in _resolved_types(spec).values():
                for primitive in ("str", "int", "float", "bool", "bytes"):
                    if value["id"] == "python." + primitive and (
                        value["usage"] != "shared"
                        or value["representation"] != primitive
                    ):
                        raise TypeCheckError(
                            f"canonical primitive identity requires its shared representation: {value['id']}"
                        )
                old = self.types.setdefault(value["id"], value)
                if old != value:
                    raise TypeCheckError(
                        f"inconsistent nominal type declaration: {value['id']}"
                    )
        lookup = {(s["id"], s["version"]): s for s in interfaces}
        for name, port in doc["ports"].items():
            ref = port["requires"]
            self.imports[name] = lookup[(ref["id"], ref["version"])]
        ref = doc["module"]["provides"]
        self.result = lookup[(ref["id"], ref["version"])]
        if imports is not None:
            self.imports, self.result = imports, result
        self.export = doc["program"].get("export", "run")
        if set(self.result["callables"]) != {self.export} or self.result["types"]:
            raise TypeCheckError(
                "a checked program implements one operation and no Python type exports"
            )
        self.signature = self.result["typing"]["operations"][self.export]
        self.parameters = []
        env = {}
        for param in self.result["callables"][self.export]["parameters"]:
            name = param["name"]
            self.name(name, env, None)
            declaration = self.signature["parameters"][name]
            value = self.result["typing"]["types"][declaration["type"]]
            if declaration["mode"] == "borrow":
                raise TypeCheckError(
                    "public checked-program inputs support share or move; borrow is scoped to operation calls"
                )
            env[name] = {"type": value["id"], "live": True}
            self.parameters.append({"name": name, **value, "mode": declaration["mode"]})
        self.returns = [
            self.result["typing"]["types"][name] for name in self.signature["returns"]
        ]
        try:
            tree = ast.parse(source, filename=doc["program"]["source"])
        except SyntaxError as error:
            raise TypeCheckError(f"line {error.lineno}: {error.msg}") from error
        if sum(1 for _ in ast.walk(tree)) > 10000:
            raise TypeCheckError("checked program exceeds 10,000 syntax nodes")
        self.ir = self.block(tree.body, env)
        allowed = set(self.signature["effects"])
        if not self.effects <= allowed:
            raise TypeCheckError(
                f"operation effect contract excludes: {sorted(self.effects - allowed)}"
            )
        if "allowed_effects" in doc["policy"] and not self.effects <= set(
            doc["policy"]["allowed_effects"]
        ):
            raise TypeCheckError(
                f"program effect policy excludes: {sorted(self.effects - set(doc['policy']['allowed_effects']))}"
            )

    def fail(self, node, message):
        raise TypeCheckError(f"line {getattr(node, 'lineno', '?')}: {message}")

    def name(self, name, env, node):
        _name(name)
        if name.startswith("_") or name in self.doc["ports"] or name == "discard":
            self.fail(node, f"reserved binding: {name}")
        if name in env:
            self.fail(node, f"binding {name!r} already exists; use a fresh name")

    def value(self, node, env, mode="share"):
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            if value is None:
                self.fail(node, f"unbound value: {node.id}")
            if not value["live"]:
                self.fail(node, f"use after move: {node.id}")
            return {"name": node.id, "type": value["type"]}
        if isinstance(node, ast.Constant) and type(node.value) in (
            str,
            int,
            float,
            bool,
            bytes,
        ):
            identity = "python." + type(node.value).__name__
            declaration = self.types.get(identity)
            if (
                declaration is None
                or declaration["usage"] != "shared"
                or declaration["representation"] != type(node.value).__name__
            ):
                self.fail(node, f"literal requires canonical shared type {identity}")
            # bytes are encoded in IR as hex to retain canonical JSON provenance.
            return {
                "literal": node.value.hex()
                if isinstance(node.value, bytes)
                else node.value,
                "type": identity,
                "bytes": isinstance(node.value, bytes),
            }
        self.fail(
            node,
            "values must be names or primitive literals; computations require declared module operations",
        )

    def use(self, expression, env, mode, node):
        declaration = self.types[expression["type"]]
        if declaration["usage"] == "shared":
            if mode != "share":
                self.fail(node, "shared values require share mode")
        elif mode == "share":
            self.fail(node, "owned values require move or borrow mode")
        elif mode == "move":
            env[expression["name"]]["live"] = False

    def call(self, node, env):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or not isinstance(node.func.value, ast.Name)
            or node.func.value.id not in self.imports
        ):
            self.fail(node, "calls must target a declared port.operation")
        port, export = node.func.value.id, node.func.attr
        spec = self.imports[port]
        operation = spec["typing"]["operations"].get(export)
        if operation is None:
            self.fail(node, f"unknown typed operation: {port}.{export}")
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
            k.arg is None for k in node.keywords
        ):
            self.fail(node, "argument expansion is outside the checked language")
        kwargs = {}
        for keyword in node.keywords:
            if keyword.arg in kwargs:
                self.fail(node, "duplicate keyword argument")
            kwargs[keyword.arg] = keyword.value
        try:
            bound = (
                signature_from_spec(spec)
                .callables[export]
                .signature.bind(*node.args, **kwargs)
            )
        except TypeError as error:
            self.fail(node, f"{port}.{export}: {error}")
        arguments, parameters, occurrences = [], [], {}
        for param in spec["callables"][export]["parameters"]:
            name = param["name"]
            declaration = operation["parameters"][name]
            expected = spec["typing"]["types"][declaration["type"]]
            argument = self.value(bound.arguments[name], env)
            if argument["type"] != expected["id"]:
                self.fail(
                    node,
                    f"nominal type mismatch for {port}.{export}.{name}: expected {expected['id']}, got {argument['type']}",
                )
            if expected["usage"] != "shared":
                occurrences.setdefault(argument["name"], []).append(declaration["mode"])
            arguments.append(argument)
            parameters.append({**expected, "mode": declaration["mode"]})
        for name, modes in occurrences.items():
            if "move" in modes and len(modes) > 1:
                self.fail(node, f"conflicting borrow/move or duplicate move: {name}")
        for argument, parameter in zip(arguments, parameters, strict=True):
            self.use(argument, env, parameter["mode"], node)
        returns = [spec["typing"]["types"][name] for name in operation["returns"]]
        self.effects.update(operation["effects"])
        self.boundary.append(
            {
                "port": port,
                "operation": export,
                "line": node.lineno,
                "effects": operation["effects"],
            }
        )
        return {
            "kind": "call",
            "port": port,
            "export": export,
            "arguments": arguments,
            "parameters": parameters,
            "returns": returns,
            "line": node.lineno,
        }

    def finish(self, node, env):
        values = (
            []
            if node.value is None
            or isinstance(node.value, ast.Constant)
            and node.value.value is None
            else list(node.value.elts)
            if isinstance(node.value, ast.Tuple)
            else [node.value]
        )
        if len(values) != len(self.returns):
            self.fail(node, f"return arity: expected {len(self.returns)} values")
        expressions = []
        for raw, expected in zip(values, self.returns, strict=True):
            expression = self.value(raw, env)
            if expression["type"] != expected["id"]:
                self.fail(
                    raw,
                    f"return type mismatch: expected {expected['id']}, got {expression['type']}",
                )
            self.use(
                expression,
                env,
                "share" if expected["usage"] == "shared" else "move",
                raw,
            )
            expressions.append(expression)
        outstanding = sorted(
            name
            for name, value in env.items()
            if value["live"] and self.types[value["type"]]["usage"] == "linear"
        )
        if outstanding:
            self.fail(
                node, f"unconsumed linear resources on normal return: {outstanding}"
            )
        return {"kind": "return", "values": expressions}

    def block(self, statements, env):
        result = []
        for position, node in enumerate(statements):
            if isinstance(node, ast.Return):
                if position != len(statements) - 1:
                    self.fail(node, "unreachable statements after return")
                result.append(self.finish(node, env))
                return result
            if isinstance(node, ast.If):
                if position != len(statements) - 1 or not node.orelse:
                    self.fail(node, "branches must be terminal and have an else path")
                condition = self.value(node.test, env)
                if condition["type"] != "python.bool":
                    self.fail(node.test, "branch conditions require python.bool")
                result.append(
                    {
                        "kind": "if",
                        "condition": condition,
                        "then": self.block(node.body, copy.deepcopy(env)),
                        "else": self.block(node.orelse, copy.deepcopy(env)),
                    }
                )
                return result
            if isinstance(node, ast.Expr):
                if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "discard"
                ):
                    if len(node.value.args) != 1 or node.value.keywords:
                        self.fail(node, "discard takes one affine value")
                    value = self.value(node.value.args[0], env)
                    if self.types[value["type"]]["usage"] != "affine":
                        self.fail(
                            node, "only affine values can be explicitly discarded"
                        )
                    self.use(value, env, "move", node)
                    result.append({"kind": "discard", "value": value})
                else:
                    call = self.call(node.value, env)
                    if call["returns"]:
                        self.fail(node, "operation results need explicit bindings")
                    result.append({**call, "targets": []})
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                raw_targets = (
                    node.targets[0].elts
                    if isinstance(node.targets[0], ast.Tuple)
                    else [node.targets[0]]
                )
                names = []
                for target in raw_targets:
                    if not isinstance(target, ast.Name):
                        self.fail(target, "assignment targets must be fresh names")
                    self.name(target.id, {**env, **dict.fromkeys(names)}, target)
                    names.append(target.id)
                if isinstance(node.value, ast.Call):
                    call = self.call(node.value, env)
                    if len(names) != len(call["returns"]):
                        self.fail(
                            node, "assignment arity differs from operation results"
                        )
                    for name, value in zip(names, call["returns"], strict=True):
                        env[name] = {"type": value["id"], "live": True}
                    result.append({**call, "targets": names})
                elif len(names) == 1:
                    value = self.value(node.value, env)
                    self.use(
                        value,
                        env,
                        "share"
                        if self.types[value["type"]]["usage"] == "shared"
                        else "move",
                        node,
                    )
                    env[names[0]] = {"type": value["type"], "live": True}
                    result.append({"kind": "bind", "target": names[0], "value": value})
                else:
                    self.fail(node, "multiple bindings require a declared operation")
            else:
                self.fail(
                    node, f"{type(node).__name__} is outside the checked language"
                )
        if self.returns:
            raise TypeCheckError(
                "every path must explicitly return the declared results"
            )
        result.append(self.finish(ast.Return(value=None), env))
        return result


def check_program(source, repository):
    """Check against published interfaces, without discovering implementations."""
    doc, text = read_program(source)
    references = [
        doc["module"]["provides"],
        *[port["requires"] for port in doc["ports"].values()],
    ]
    specs = {
        (ref["id"], ref["version"]): validate_interface(
            repository.interface(ref["id"], ref["version"])
        )
        for ref in references
    }
    interfaces = [specs[key] for key in sorted(specs)]
    from .instance_terms import validate_instance_interface

    instance_contract = {
        "requires": {name: port["requires"] for name, port in doc["ports"].items()},
        "instance_sharing": doc.get("instance_sharing", []),
        "instance_exports": doc.get("instance_exports", {}),
    }
    public = doc["module"]["provides"]
    try:
        validate_instance_interface(instance_contract, specs[(public["id"], public["version"])], {
            name: specs[(port["requires"]["id"], port["requires"]["version"])] for name, port in doc["ports"].items()
        })
    except ValueError as error:
        raise TypeCheckError(str(error)) from error
    from .typed_associated import prepare

    try:
        imports, result, assumptions = prepare(doc, interfaces)
        checker = _Checker(doc, text, [*imports.values(), result], imports, result)
    except ValueError as error:
        raise TypeCheckError(str(error)) from error
    report = {
        "format": FORMAT,
        "document": doc,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "interfaces": interfaces,
        "associated_assumptions": assumptions,
        "instance_contract": instance_contract,
        "parameters": checker.parameters,
        "returns": checker.returns,
        "types": checker.types,
        "effects": sorted(checker.effects),
        "ir": checker.ir,
        "trusted_operations": checker.boundary,
        "guarantees": {
            "nominal_value_types": True,
            "normal_path_usage": True,
            "lexical_call_borrows": True,
            "declared_effect_bound": True,
            "python_implementation_verified": False,
            "exception_cleanup_verified": False,
        },
    }
    report["sha256"] = hashlib.sha256(canonical_bytes(report)).hexdigest()
    return report


def _python(report):
    doc = report["document"]
    specs = {(spec["id"], spec["version"]): spec for spec in report["interfaces"]}
    names = sorted(doc["ports"])
    lines = [
        "from module_families.ownership import invoke as _invoke, move_owned as _move, drop_owned as _drop, mark_checked as _checked",
        "from module_families.contracts import Requirement as _Requirement",
        "from module_families.typed_associated import specialize_runtime as _specialize, descriptors as _descriptors",
        "from module_families.interfaces import signature_from_spec as _signature",
        "from module_families.instance_terms import resolve_instances as _instances",
        "",
        "def create(" + ("*, " + ", ".join(names) if names else "") + "):",
    ]
    for name in names:
        ref = doc["ports"][name]["requires"]
        lines.append(
            f"    _Requirement(_signature({specs[(ref['id'], ref['version'])]!r})).check({name}, {name!r})"
        )
    ports = "{" + ", ".join(repr(name) + ": " + name for name in names) + "}"
    lines.append(f"    _instances({report['instance_contract']!r}, {{name: module.metadata() for name, module in {ports}.items()}})")
    lines.append(f"    _identities = _specialize({report['associated_assumptions']!r}, {ports})")
    lines.append("    def _d(values): return _descriptors(values, _identities)")
    params = report["parameters"]
    export = doc["program"].get("export", "run")
    # Preserve the public call shape, including positional-only parameters.
    ref = doc["module"]["provides"]
    shape = (
        signature_from_spec(specs[(ref["id"], ref["version"])])
        .callables[export]
        .signature
    )
    lines.append(f"    def _entry{shape}:")
    if params:
        arguments = ", ".join(p["name"] for p in params)
        returns = [
            {key: value for key, value in p.items() if key not in {"name", "mode"}}
            for p in params
        ]
        lines.append(
            f"        {arguments}, = _invoke(lambda *items: items[0] if len(items) == 1 else items, [{arguments}], _d({params!r}), _d({returns!r}))"
        )

    def value(expr, move=False):
        if "name" in expr:
            name = expr["name"]
            return (
                f"_move({name})"
                if move and report["types"][expr["type"]]["usage"] != "shared"
                else name
            )
        if expr["bytes"]:
            return repr(bytes.fromhex(expr["literal"]))
        return repr(expr["literal"])

    def emit(statements, indent):
        for statement in statements:
            kind = statement["kind"]
            if kind == "call":
                prefix = (
                    ", ".join(statement["targets"]) + ", = "
                    if statement["targets"]
                    else ""
                )
                args = ", ".join(value(v) for v in statement["arguments"])
                lines.append(
                    indent
                    + prefix
                    + f"_invoke({statement['port']}[{statement['export']!r}], [{args}], _d({statement['parameters']!r}), _d({statement['returns']!r}))"
                )
            elif kind == "bind":
                lines.append(
                    indent
                    + f"{statement['target']} = {value(statement['value'], True)}"
                )
            elif kind == "discard":
                lines.append(indent + f"_drop({value(statement['value'])})")
            elif kind == "return":
                values = [value(v, True) for v in statement["values"]]
                lines.append(
                    indent + ("return " + ", ".join(values) if values else "return")
                )
            elif kind == "if":
                lines.append(indent + f"if {value(statement['condition'])}:")
                emit(statement["then"], indent + "    ")
                lines.append(indent + "else:")
                emit(statement["else"], indent + "    ")

    emit(report["ir"], "        ")
    lines.append(
        f"    return {{{export!r}: _checked(_entry, _d({params!r}), _d({report['returns']!r}))}}"
    )
    result = "\n".join(lines) + "\n"
    compile(result, "<checked module>", "exec")
    return result


def build_program(source, repository, out):
    report = check_program(source, repository)
    # Stabilize code bytes against irrelevant TOML map ordering.
    report = json.loads(canonical_bytes(report))
    generated = _python(report)
    digest = hashlib.sha256(
        canonical_bytes(
            {"certificate": report["sha256"], "code": generated, "runtime": __version__}
        )
    ).hexdigest()
    out = Path(out).resolve()
    package = "mf_checked_" + digest
    root = out / "source"
    (root / package).mkdir(parents=True, exist_ok=True)
    (root / package / "__init__.py").write_text(generated)
    doc = report["document"]
    member = {
        "id": doc["module"]["id"],
        "version": doc["module"]["version"],
        "kind": "functor" if doc["ports"] else "module-factory",
        "symbol": f"{package}:create",
        "summary": doc["module"].get("summary", "Separately checked module program"),
        "provides": doc["module"]["provides"],
        "requires": {name: port["requires"] for name, port in doc["ports"].items()},
        "effects": sorted(
            set(report["effects"]) | ({"dependency-effects"} if doc["ports"] else set())
        ),
        "capabilities": doc["module"].get("capabilities", []),
        "implementation_trust": "checked-composition",
        "associated": report["associated_assumptions"]["associated"],
        "instance_sharing": report["instance_contract"]["instance_sharing"],
        "instance_exports": report["instance_contract"]["instance_exports"],
        "checked_program": {
            "format": FORMAT,
            "certificate_sha256": report["sha256"],
            "source_sha256": report["source_sha256"],
            "normal_return_only": True,
        },
    }
    family = doc["family"]
    manifest = {
        "schema_version": 1,
        **family,
        "publisher": doc["publisher"]["name"],
        "context": family.get("context", {}),
        "source": {"root": str(root), "package": package},
        "external_dependencies": {"module_families": f"module-families=={__version__}"},
        "members": [member],
    }
    path = out / "family.toml"
    write_manifest(manifest, path)
    index = build_family(path, out)
    (out / "generated.py").write_text(generated)
    (out / "certificate.json").write_bytes(canonical_bytes(report))
    (out / "work-contract.json").write_bytes(
        canonical_bytes(
            {
                "provides": member["provides"],
                "requires": member["requires"],
                "interfaces": report["interfaces"],
                "associated_assumptions": report["associated_assumptions"],
                "instance_contract": report["instance_contract"],
                "guarantees": report["guarantees"],
                "trusted_operations": report["trusted_operations"],
            }
        )
    )
    return {
        "index": str(out / "index.json"),
        "certificate": str(out / "certificate.json"),
        "member": index["members"][0]["id"],
        "certificate_sha256": report["sha256"],
        "effects": report["effects"],
        "artifacts": len(index["artifacts"]),
    }
