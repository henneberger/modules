"""Import-free family metadata, validation, and bounded discovery."""

from __future__ import annotations

import copy
import json
import keyword
import re
from pathlib import Path
from typing import Any

from .interfaces import InterfaceError, validate_interface, validate_interface_reference


class ManifestError(ValueError):
    """A family document cannot be interpreted unambiguously."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _contract_id(member: dict) -> str | None:
    reference = member.get("provides")
    return reference.get("id") if isinstance(reference, dict) else None


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(s, str) or not s.strip() for s in value
    ):
        raise ManifestError(f"{field} must be a list of nonempty strings")
    return value


def _identifier(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.isidentifier()
        or keyword.iskeyword(value)
    ):
        raise ManifestError(f"{label} must be a Python identifier")


def _symbol(value: Any, identifier: str, packages: list[str]) -> None:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ManifestError(f"{identifier}: symbol must be module:definition")
    module, name = value.split(":")
    if not all(
        part.isidentifier() and not keyword.iskeyword(part)
        for part in module.split(".")
    ):
        raise ManifestError(f"{identifier}: invalid Python symbol")
    _identifier(name, f"{identifier} symbol export")
    if not any(
        module == package or module.startswith(package + ".") for package in packages
    ):
        raise ManifestError(
            f"{identifier}: symbol is outside source.package or declared sources"
        )


def _references(member: dict, identifier: str) -> None:
    from .associated import validate_associated
    from .indices import validate_indices
    from .instance_terms import validate_instances

    try:
        validate_indices(member)
        validate_associated(member)
        validate_instances(member)
        from .mixins import validate_mixin

        validate_mixin(member)
    except ValueError as error:
        raise ManifestError(f"{identifier}: {error}") from error
    try:
        if "provides" in member:
            validate_interface_reference(member["provides"])
        requires = member.get("requires", {})
        if not isinstance(requires, dict):
            raise ManifestError(
                f"{identifier}.requires must map named slots to exact interface references"
            )
        for slot, reference in requires.items():
            _identifier(slot, f"{identifier} requirement slot")
            validate_interface_reference(reference)
    except InterfaceError as error:
        raise ManifestError(f"{identifier}: {error}") from error
    if "contract_id" in member or "contract" in member:
        raise ManifestError(
            f"{identifier}: declare an exact provides reference instead of contract aliases"
        )
    sharing = member.get("sharing", [])
    if not isinstance(sharing, (list, tuple)):
        raise ManifestError(
            f"{identifier}.sharing must be a list of type-sharing groups"
        )
    for group in sharing:
        if not isinstance(group, (list, tuple)) or len(group) < 2:
            raise ManifestError(
                f"{identifier}.sharing groups need at least two slot.Type references"
            )
        for reference in group:
            if not isinstance(reference, str) or reference.count(".") != 1:
                raise ManifestError(
                    f"{identifier}.sharing requires slot.Type references"
                )
            slot, name = reference.split(".")
            _identifier(name, f"{identifier} shared type")
            if slot not in requires:
                raise ManifestError(
                    f"{identifier}.sharing refers to unknown requirement slot {slot!r}"
                )


def validate_manifest(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ManifestError("expected family schema_version 1")
    for field in ("name", "version", "description"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ManifestError(f"{field} must be a nonempty string")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", document["name"]):
        raise ManifestError(
            "family name must contain lowercase letters, digits, and hyphens"
        )
    if not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+|\.post\d+)?", document["version"]
    ):
        raise ManifestError(
            "version must use a supported PEP 440 release such as 0.1.0"
        )
    if not isinstance(document.get("context"), dict):
        raise ManifestError("context must be an object")
    publisher = document.get("publisher")
    if not isinstance(publisher, str) or not re.fullmatch(
        r"[a-z][a-z0-9-]*", publisher
    ):
        raise ManifestError("publisher must be a lowercase namespace string")
    interfaces = document.get("interfaces", [])
    if not isinstance(interfaces, list):
        raise ManifestError("interfaces must be a list of interface declarations")
    interface_ids = set()
    for interface in interfaces:
        try:
            normalized_interface = validate_interface(interface)
        except InterfaceError as error:
            raise ManifestError(f"invalid interface: {error}") from error
        reference = (normalized_interface["id"], normalized_interface["version"])
        if reference in interface_ids:
            raise ManifestError(
                f"duplicate interface declaration: {reference[0]}@{reference[1]}"
            )
        interface_ids.add(reference)
    members = document.get("members")
    if not isinstance(members, list) or (not members and not interfaces):
        raise ManifestError(
            "members must be a nonempty list unless interfaces are declared"
        )
    if "source" in document and "sources" in document:
        raise ManifestError("source and sources are mutually exclusive")
    sources = document.get("sources", {})
    if not isinstance(sources, dict):
        raise ManifestError("sources must map source aliases to source declarations")
    for alias in sources:
        if not isinstance(alias, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", alias
        ):
            raise ManifestError(f"invalid source alias: {alias!r}")
    if "source" in document:
        sources = {"source": document["source"]}
    if members and not sources:
        raise ManifestError(
            "source.root and source.package or sources are required for members"
        )
    packages = []
    for alias, source in sources.items():
        if not isinstance(source, dict) or not all(
            isinstance(source.get(f), str) and source[f] for f in ("root", "package")
        ):
            raise ManifestError(f"{alias}: source.root and source.package are required")
        package = source["package"]
        if not all(
            part.isidentifier() and not keyword.iskeyword(part)
            for part in package.split(".")
        ):
            raise ManifestError(f"{alias}: source.package must be a Python module path")
        if any(
            package == other
            or package.startswith(other + ".")
            or other.startswith(package + ".")
            for other in packages
        ):
            raise ManifestError(f"ambiguous or overlapping source.package: {package}")
        _strings(source.get("license_files", []), f"{alias}.license_files")
        if "modules" in source:
            modules = _strings(source["modules"], f"{alias}.modules")
            if not modules or len(set(modules)) != len(modules):
                raise ManifestError(
                    f"{alias}.modules must be a nonempty list of distinct module names"
                )
            for module in modules:
                if not all(
                    part.isidentifier() and not keyword.iskeyword(part)
                    for part in module.split(".")
                ):
                    raise ManifestError(
                        f"{alias}.modules must contain Python module names"
                    )
                if module != package and not module.startswith(package + "."):
                    raise ManifestError(
                        f"{alias}.modules contains a module outside source.package: {module}"
                    )
        packages.append(package)
    ids: set[str] = set()
    distributions: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            raise ManifestError("each member must be an object")
        identifier = member.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.-]*", identifier
        ):
            raise ManifestError(f"invalid member id: {identifier!r}")
        normalized = re.sub(r"[-_.]+", "-", identifier).lower()
        if publisher is not None:
            local_id = member.get("local_id")
            if (
                member.get("publisher") != publisher
                or not isinstance(local_id, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", local_id)
                or identifier != publisher + "." + local_id
            ):
                raise ManifestError(
                    f"{identifier}: publisher, local_id, and qualified id must agree"
                )
        elif "publisher" in member or "local_id" in member:
            raise ManifestError(
                f"{identifier}: member ownership requires a root publisher"
            )
        if identifier in ids or normalized in distributions:
            raise ManifestError(
                f"duplicate or distribution-colliding member: {identifier}"
            )
        ids.add(identifier)
        distributions.add(normalized)
        if member.get("kind") == "module":
            exports = member.get("exports")
            if not isinstance(exports, dict) or not exports:
                raise ManifestError(
                    f"{identifier}: a module requires a nonempty exports mapping"
                )
            for public_name, symbol in exports.items():
                _identifier(public_name, f"{identifier} public export")
                _symbol(symbol, identifier, packages)
            if "symbol" in member:
                _symbol(member["symbol"], identifier, packages)
        else:
            if "exports" in member:
                raise ManifestError(f"{identifier}: exports requires kind='module'")
            _symbol(member.get("symbol"), identifier, packages)
        if member.get("kind") not in {
            "algorithm",
            "operation",
            "type",
            "adapter",
            "contract",
            "module-factory",
            "module",
            "functor",
        }:
            raise ManifestError(f"{identifier}: invalid kind")
        if not isinstance(member.get("summary"), str) or not member["summary"].strip():
            raise ManifestError(f"{identifier}: summary is required")
        for field in ("tags", "solves", "use_when", "avoid_when", "effects"):
            _strings(
                member.get(field, [] if field != "effects" else ["unknown"]),
                f"{identifier}.{field}",
            )
        if "version" in member and (
            not isinstance(member["version"], str)
            or not re.fullmatch(
                r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+|\.post\d+)?", member["version"]
            )
        ):
            raise ManifestError(f"{identifier}: invalid member version")
        if "unknown" in member.get("effects", []) and len(member["effects"]) != 1:
            raise ManifestError(
                f"{identifier}: unknown effects cannot be mixed with known effects"
            )
        _references(member, identifier)
    for field in ("external_dependencies", "dynamic_dependencies"):
        mapping = document.get(field, {})
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str) for k in mapping
        ):
            raise ManifestError(f"{field} must be an object")
        for key, value in mapping.items():
            if field == "dynamic_dependencies":
                _strings(value, f"{field}.{key}")
            elif not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{field}.{key} must be a requirement string")


class Catalog:
    """A family manifest; querying this object never imports its source package."""

    def __init__(self, document: dict, path: Path | None = None):
        validate_manifest(document)
        self._document = copy.deepcopy(document)
        if "interfaces" in self._document:
            self._document["interfaces"] = [
                validate_interface(spec) for spec in self._document["interfaces"]
            ]
        for member in self._document["members"]:
            for field in ("tags", "solves", "use_when", "avoid_when"):
                member.setdefault(field, [])
            member.setdefault("effects", ["unknown"])
        self.path = path
        self._members = {m["id"]: m for m in self._document["members"]}

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        from .manifest import load_manifest

        path = Path(path).resolve()
        return cls(load_manifest(path), path)

    @property
    def document(self) -> dict:
        return copy.deepcopy(self._document)

    def inspect(self, identifier: str) -> dict:
        try:
            card = copy.deepcopy(self._members[identifier])
        except KeyError as exc:
            raise ManifestError(f"unknown member: {identifier}") from exc
        card["family"] = self._document["name"]
        card["version"] = card.get("version", self._document["version"])
        card["family_version"] = self._document["version"]
        card["family_context"] = copy.deepcopy(self._document["context"])
        return card

    def search(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        contract: str | None = None,
        allowed_effects: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ManifestError("limit must be 1..100 and offset must be nonnegative")
        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        results = []
        for identifier, member in self._members.items():
            if kind is not None and member["kind"] != kind:
                continue
            if contract is not None and _contract_id(member) != contract:
                continue
            if allowed_effects is not None and (
                "unknown" in member["effects"]
                or not set(member["effects"]).issubset(allowed_effects)
            ):
                continue
            text = " ".join(
                [
                    identifier,
                    member["summary"],
                    *member["tags"],
                    *member["solves"],
                    *member["use_when"],
                ]
            ).lower()
            tokens = set(re.findall(r"[a-z0-9]+", text))
            matched = terms & tokens
            if terms and not matched:
                continue
            score = len(matched) / len(terms) if terms else 0.0
            if query and query.lower() in identifier.lower():
                score += 1.0
            results.append(
                {
                    "id": identifier,
                    "family": self._document["name"],
                    "version": member.get("version", self._document["version"]),
                    "kind": member["kind"],
                    "summary": member["summary"],
                    "tags": member["tags"],
                    "effects": member["effects"],
                    "contract_id": _contract_id(member),
                    "score": score,
                    "matched_terms": sorted(matched),
                }
            )
        results.sort(key=lambda row: (-row["score"], row["id"]))
        return copy.deepcopy(results[offset : offset + limit])
