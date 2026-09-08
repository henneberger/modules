"""Compact TOML authoring and static expansion of Python contributions.

An authored manifest contains discovery rules and exceptional metadata. The
expanded catalog is an in-memory build input, never an authored source index.
"""

from __future__ import annotations

import ast
import copy
import json
import keyword
import math
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from .interfaces import InterfaceError, validate_interface


class ManifestFormatError(ValueError):
    """An invalid source manifest, contribution or discovery rule."""


def _error(origin: Path, message: str) -> ManifestFormatError:
    return ManifestFormatError(f"{origin}: {message}")


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise _error(path, f"cannot read TOML: {error}") from error
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise _error(
            path,
            "manifest values must be finite JSON-compatible data; quote dates and times",
        ) from error
    return document


def _table(value: Any, name: str, origin: Path) -> dict:
    if not isinstance(value, dict):
        raise _error(origin, f"{name} must be a table")
    return value


def _patterns(value: Any, name: str, origin: Path) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise _error(
            origin, f"{name} must be a list of nonempty relative glob patterns"
        )
    for pattern in value:
        path = PurePosixPath(pattern)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in pattern
            or re.match(r"^[A-Za-z]:", pattern)
        ):
            raise _error(
                origin,
                f"{name}: absolute paths and traversal are forbidden: {pattern!r}",
            )
    return value


def _matches(
    base: Path,
    patterns: list[str],
    origin: Path,
    *,
    suffix: str,
    label: str,
    reject_symlinks: bool = False,
) -> list[Path]:
    """Expand safely, allowing an empty wildcard for a new contribution tree."""
    root = base.resolve()
    if reject_symlinks and base != root:
        raise _error(origin, f"{label} source root uses a symlink: {base}")
    found: dict[Path, Path] = {}
    for pattern in sorted(patterns):
        try:
            matches = sorted(base.glob(pattern))
        except (OSError, ValueError, NotImplementedError) as error:
            raise _error(
                origin, f"invalid {label} glob {pattern!r}: {error}"
            ) from error
        files = []
        for path in matches:
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                raise _error(
                    origin, f"{label} path escapes its root through a symlink: {path}"
                )
            if reject_symlinks and path != path.resolve():
                raise _error(
                    origin,
                    f"{label} source paths cannot use symlinks: {path}; "
                    "publish the defining Python module path explicitly",
                )
            if path.suffix != suffix:
                raise _error(origin, f"{label} includes a non-{suffix} file: {path}")
            files.append(path)
        if not files and not any(character in pattern for character in "*?["):
            raise _error(
                origin, f"{label} include must match a {suffix} file: {pattern!r}"
            )
        for path in files:
            # Overlapping globs or an internal symlink must not include the
            # same physical contribution twice.
            found.setdefault(path.resolve(), path)
    return sorted(found.values(), key=lambda path: path.relative_to(base).as_posix())


def _default_card(identifier: str, symbol: str) -> dict:
    return {
        "id": identifier,
        "symbol": symbol,
        "kind": "operation",
        "summary": f"Public definition {symbol}.",
        "effects": ["unknown"],
        "tags": [],
    }


def _discover_source(document: dict, origin: Path, prefix: str = "") -> dict[str, dict]:
    discovery = _table(document.get("discovery", {}), "discovery", origin)
    public = discovery.get("public", False)
    if type(public) is not bool:
        raise _error(origin, "discovery.public must be a boolean")
    includes = _patterns(
        discovery.get("include", ["**/*.py"]), "discovery.include", origin
    )
    excludes = _patterns(discovery.get("exclude", []), "discovery.exclude", origin)
    unknown = set(discovery) - {"public", "include", "exclude"}
    if unknown:
        raise _error(origin, f"unknown discovery fields: {sorted(unknown)}")
    if not public:
        return {}
    source = _table(document.get("source", {}), "source", origin)
    package = source.get("package")
    root = source.get("root")
    if not isinstance(root, str) or not root:
        raise _error(origin, "source.root is required for discovery")
    if not isinstance(package, str) or not all(
        part.isidentifier() and not keyword.iskeyword(part)
        for part in package.split(".")
    ):
        raise _error(origin, "source.package must be an importable Python module path")
    package_root = (origin.parent / root).resolve().joinpath(*package.split("."))
    if not package_root.is_dir():
        raise _error(origin, f"source package directory does not exist: {package_root}")
    modules = source.get("modules")
    if modules is not None:
        if (
            not isinstance(modules, list)
            or not modules
            or any(not isinstance(module, str) for module in modules)
            or len(set(modules)) != len(modules)
        ):
            raise _error(
                origin,
                "source.modules must be a nonempty list of distinct module names",
            )
        for module in modules:
            if not all(
                part.isidentifier() and not keyword.iskeyword(part)
                for part in module.split(".")
            ) or (module != package and not module.startswith(package + ".")):
                raise _error(
                    origin,
                    f"source.modules contains an invalid or outside-package module: {module!r}",
                )
    included = _matches(
        package_root,
        includes,
        origin,
        suffix=".py",
        label="discovery",
        reject_symlinks=True,
    )
    # Exclusions are match predicates, so a currently absent excluded path is
    # useful when a contributor introduces that path later.
    excluded = set()
    for pattern in excludes:
        excluded.update(
            path.resolve() for path in package_root.glob(pattern) if path.is_file()
        )
    members: dict[str, dict] = {}
    definitions: dict[str, str] = {}
    for path in included:
        if path.resolve() in excluded:
            continue
        relative = path.relative_to(package_root)
        if any(
            part.startswith("_") and part != "__init__.py" for part in relative.parts
        ):
            continue
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join([package, *parts])
        if modules is not None and module not in modules:
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, ValueError) as error:
            raise _error(path, f"cannot inspect Python source: {error}") from error
        for node in tree.body:
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) or node.name.startswith("_"):
                continue
            identifier = ".".join([*([prefix] if prefix else []), *parts, node.name])
            symbol = f"{module}:{node.name}"
            if identifier in members:
                raise _error(
                    path,
                    f"duplicate discovered member {identifier!r}; first defined in {definitions[identifier]}",
                )
            card = _default_card(identifier, symbol)
            if isinstance(node, ast.ClassDef):
                protocol = any(
                    (isinstance(base, ast.Name) and base.id == "Protocol")
                    or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
                    or (
                        isinstance(base, ast.Subscript)
                        and isinstance(base.value, ast.Name)
                        and base.value.id == "Protocol"
                    )
                    for base in node.bases
                )
                card["kind"] = "contract" if protocol else "type"
            summary = " ".join(
                (ast.get_docstring(node) or "").split("\n\n", 1)[0].split()
            )
            if summary:
                card["summary"] = (
                    summary if len(summary) <= 240 else summary[:237] + "..."
                )
            card["tags"] = [*([prefix] if prefix else []), *parts[:1]]
            members[identifier] = card
            definitions[identifier] = str(path)
    return members


def _discover(document: dict, origin: Path) -> dict[str, dict]:
    sources = document.get("sources")
    if sources is None:
        return _discover_source(document, origin)
    result = {}
    for alias, source in sorted(sources.items()):
        discovered = _discover_source(
            {**document, "source": source},
            origin,
            prefix=alias,
        )
        overlap = set(discovered) & set(result)
        if overlap:
            raise _error(
                origin,
                f"duplicate discovered member IDs across sources: {sorted(overlap)}",
            )
        result.update(discovered)
    return result


def _entries(value: Any, origin: Path) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _error(origin, "members must be an array of [[members]] tables")
    for member in value:
        if not isinstance(member.get("id"), str) or not member["id"].strip():
            raise _error(origin, "each [[members]] table requires a nonempty id")
    return value


def _expand(document: dict, origin: Path) -> dict:
    allowed = {
        "schema_version",
        "family",
        "source",
        "sources",
        "discovery",
        "context",
        "external_dependencies",
        "dynamic_dependencies",
        "contributions",
        "members",
        "interfaces",
        "publisher",
    }
    unknown = set(document) - allowed
    if unknown:
        raise _error(origin, f"unknown root fields: {sorted(unknown)}")
    if document.get("schema_version") != 1:
        raise _error(origin, "schema_version must be 1")
    family = _table(document.get("family"), "family", origin)
    for field in ("name", "version", "description"):
        if not isinstance(family.get(field), str) or not family[field].strip():
            raise _error(origin, f"family.{field} must be a nonempty string")
    declaration = _table(document.get("publisher"), "publisher", origin)
    publisher = declaration.get("name")
    if (
        set(declaration) != {"name"}
        or not isinstance(publisher, str)
        or not re.fullmatch(r"[a-z][a-z0-9-]*", publisher)
    ):
        raise _error(
            origin,
            "publisher must contain a lowercase name using letters, digits, and hyphens",
        )
    if "source" in document and "sources" in document:
        raise _error(origin, "source and sources are mutually exclusive")
    sources = _table(document.get("sources", {}), "sources", origin)
    if "source" in document:
        sources = {"source": _table(document["source"], "source", origin)}
    for alias, source in sources.items():
        if not isinstance(alias, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", alias
        ):
            raise _error(origin, f"invalid source alias: {alias!r}")
        source = _table(source, f"sources.{alias}", origin)
        for field in ("root", "package"):
            if not isinstance(source.get(field), str) or not source[field]:
                raise _error(origin, f"{alias}.{field} must be a nonempty string")
    for key in ("context", "external_dependencies", "dynamic_dependencies"):
        _table(document.get(key, {}), key, origin)
    contributions = _table(document.get("contributions", {}), "contributions", origin)
    if set(contributions) - {"include"}:
        raise _error(origin, "contributions supports only include")
    patterns = _patterns(
        contributions.get("include", []), "contributions.include", origin
    )
    members = _discover(document, origin)
    explicit: dict[str, Path] = {}
    interfaces: dict[tuple[str, str], dict] = {}
    interface_origins: dict[tuple[str, str], Path] = {}

    def merge_interfaces(entries: Any, path: Path) -> None:
        if not isinstance(entries, list):
            raise _error(path, "interfaces must be an array of [[interfaces]] tables")
        for entry in entries:
            try:
                spec = validate_interface(entry)
            except InterfaceError as error:
                raise _error(path, str(error)) from error
            key = (spec["id"], spec["version"])
            if key in interfaces:
                raise _error(
                    path,
                    f"duplicate interface {key[0]}@{key[1]}; first declared in {interface_origins[key]}",
                )
            interfaces[key] = spec
            interface_origins[key] = path

    def merge(entries: list[dict], path: Path) -> None:
        for entry in entries:
            entry = copy.deepcopy(entry)
            identifier = entry["id"]
            if publisher:
                identifier = identifier.removeprefix(publisher + ".")
                if not identifier:
                    raise _error(
                        path, "publisher-qualified member id needs a local name"
                    )
                if entry.get("publisher", publisher) != publisher:
                    raise _error(
                        path,
                        "member.publisher cannot impersonate a different publisher",
                    )
                if entry.get("local_id", identifier) != identifier:
                    raise _error(path, "member.local_id contradicts its qualified id")
                entry["id"] = identifier
            if identifier in explicit:
                raise _error(
                    path,
                    f"duplicate explicit member {identifier!r}; first declared in {explicit[identifier]}",
                )
            explicit[identifier] = path
            if identifier not in members:
                symbol = entry.get("symbol")
                if entry.get("kind") == "module":
                    members[identifier] = {
                        "id": identifier,
                        "kind": "module",
                        "summary": f"Module {identifier}.",
                        "effects": ["unknown"],
                        "tags": [],
                    }
                elif not isinstance(symbol, str) or not symbol:
                    raise _error(
                        path,
                        f"unknown member {identifier!r} has no symbol; enable discovery or provide symbol",
                    )
                else:
                    members[identifier] = _default_card(identifier, symbol)
            if entry.get("kind") == "module" and "symbol" not in entry:
                members[identifier].pop("symbol", None)
            members[identifier] = {**members[identifier], **copy.deepcopy(entry)}

    merge_interfaces(document.get("interfaces", []), origin)
    merge(_entries(document.get("members", []), origin), origin)
    for path in _matches(
        origin.parent, patterns, origin, suffix=".toml", label="contribution"
    ):
        fragment = _read_toml(path)
        if not fragment or set(fragment) - {"members", "interfaces"}:
            raise _error(
                path,
                "contribution fragments may contain only [[members]] and [[interfaces]] tables",
            )
        merge(_entries(fragment.get("members", []), path), path)
        merge_interfaces(fragment.get("interfaces", []), path)
    expanded = {
        "schema_version": 1,
        **copy.deepcopy(family),
        "context": copy.deepcopy(document.get("context", {})),
        "external_dependencies": copy.deepcopy(
            document.get("external_dependencies", {})
        ),
        "dynamic_dependencies": copy.deepcopy(document.get("dynamic_dependencies", {})),
        "members": [members[identifier] for identifier in sorted(members)],
    }
    for key in ("source", "sources"):
        if key in document:
            expanded[key] = copy.deepcopy(document[key])
    if publisher:
        expanded["publisher"] = publisher
        for member in expanded["members"]:
            member["local_id"] = member["id"]
            member["id"] = publisher + "." + member["id"]
            member["publisher"] = publisher
    if interfaces or "interfaces" in document:
        expanded["interfaces"] = [interfaces[key] for key in sorted(interfaces)]
    from .catalog import ManifestError, validate_manifest

    try:
        validate_manifest(expanded)
    except ManifestError as error:
        raise _error(origin, str(error)) from error
    return expanded


def load_manifest(path: str | Path) -> dict:
    """Expand a compact standalone or pyproject TOML publication declaration.

    Source roots remain relative to the root manifest. Contribution fragment
    paths are always rooted there and cannot escape it through symlinks.
    """
    path = Path(path).absolute()
    if path.suffix.lower() != ".toml":
        raise _error(
            path,
            "authoring manifests must be TOML; JSON is reserved for generated data",
        )
    document = _read_toml(path)
    tool = document.get("tool", {})
    nested = tool.get("module-families") if isinstance(tool, dict) else None
    if nested is not None:
        if "family" in document:
            raise _error(
                path,
                "both standalone family and tool.module-families configurations are present",
            )
        document = _table(nested, "tool.module-families", path)
    elif path.name == "pyproject.toml":
        raise _error(path, "pyproject.toml has no [tool.module-families] configuration")
    return _expand(document, path)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value if item is not None]
    return value


def _author_document(document: dict) -> dict:
    """Render authored TOML data or the normalized in-memory catalog schema."""
    value = _clean(copy.deepcopy(document))
    if isinstance(value.get("publisher"), str):
        value["publisher"] = {"name": value["publisher"]}
    if (
        all(key in value for key in ("name", "version", "description"))
        and "family" not in value
    ):
        family = {key: value.pop(key) for key in ("name", "version", "description")}
        value = {
            "schema_version": value.pop("schema_version", 1),
            "family": family,
            **value,
        }
    return value


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _key(value: Any) -> str:
    if not isinstance(value, str):
        raise ManifestFormatError("TOML keys must be strings")
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else _quote(value)


def _value(value: Any) -> str:
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ManifestFormatError("TOML manifest numbers must be finite")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{_key(key)} = {_value(item)}" for key, item in sorted(value.items())
            )
            + " }"
        )
    raise ManifestFormatError(
        f"Cannot serialize {type(value).__name__} as manifest TOML"
    )


def dumps_toml(document: dict) -> str:
    """Serialize manifest data deterministically, omitting None values.

    This writes the document supplied by the author. It never discovers source
    declarations or inserts an expanded inventory into a discovery manifest.
    """
    if not isinstance(document, dict):
        raise ManifestFormatError("TOML document must be a mapping")
    document = _author_document(document)
    lines: list[str] = []
    preferred = [
        "schema_version",
        "family",
        "publisher",
        "source",
        "sources",
        "discovery",
        "context",
        "external_dependencies",
        "dynamic_dependencies",
        "contributions",
        "interfaces",
        "members",
    ]

    def order(mapping: dict) -> list[str]:
        return sorted(
            mapping,
            key=lambda key: (
                preferred.index(key) if key in preferred else len(preferred),
                key,
            ),
        )

    def table(mapping: dict, path: list[str]) -> None:
        if path:
            lines.extend(["[" + ".".join(_key(key) for key in path) + "]"])
        nested = []
        arrays = []
        for key in order(mapping):
            item = mapping[key]
            if isinstance(item, dict):
                nested.append((key, item))
            elif (
                isinstance(item, list)
                and item
                and all(isinstance(row, dict) for row in item)
            ):
                arrays.append((key, item))
            else:
                lines.append(f"{_key(key)} = {_value(item)}")
        if lines and lines[-1]:
            lines.append("")
        for key, item in nested:
            table(item, [*path, key])
        for key, rows in arrays:
            heading = ".".join(_key(part) for part in [*path, key])
            for row in rows:
                lines.append(f"[[{heading}]]")
                for name in sorted(row):
                    lines.append(f"{_key(name)} = {_value(row[name])}")
                lines.append("")

    table(document, [])
    return "\n".join(lines).rstrip() + "\n"


def write_manifest(document: dict, path: str | Path) -> Path:
    """Write TOML; preserve other pyproject tables semantically.

    Updating a pyproject rewrites its formatting and comments, while retaining
    parsed values of unrelated tables. Use a standalone file to preserve them.
    """
    path = Path(path)
    if path.suffix.lower() != ".toml":
        raise _error(
            path,
            "authoring manifests must be TOML; JSON is reserved for generated data",
        )
    if path.name == "pyproject.toml" and "tool" not in document:
        existing = _read_toml(path) if path.exists() else {}
        tool = existing.setdefault("tool", {})
        if not isinstance(tool, dict):
            raise _error(path, "existing tool configuration is not a table")
        tool["module-families"] = _author_document(document)
        content = dumps_toml(existing)
    else:
        content = dumps_toml(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
