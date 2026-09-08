"""Serializable interface declarations, interpreted without eval or imports.

Interfaces describe accepted call shapes and named nominal type exports. Optional
typing metadata declares the assumptions used by the checked composition language.
Validation does not verify resource usage or effects inside Python implementations.
Interfaces do not deserialize Python types, evaluate annotations or defaults,
import an implementation, or prove returned values and behavioral laws.
"""

from __future__ import annotations

import inspect
import json
import keyword
from typing import Any

from .contracts import CallableSpec, ContractError, Signature


class InterfaceError(ContractError):
    """An interface declaration is malformed or has an impossible call shape."""


_KINDS = {
    kind.name: kind
    for kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.VAR_KEYWORD,
    )
}


def _name(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isidentifier()
        or keyword.iskeyword(value)
    ):
        raise InterfaceError(f"{label} must be a Python identifier")
    return value


def _shape(value: Any, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise InterfaceError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise InterfaceError(f"{label} has unknown fields: {sorted(unknown, key=str)}")
    return value


def validate_interface_reference(reference: Any) -> dict[str, str]:
    """Validate an exact interface ID/version reference; ranges are unsupported."""
    reference = _shape(reference, {"id", "version"}, "interface reference")
    for key in ("id", "version"):
        value = reference.get(key)
        if not isinstance(value, str) or not value or value != value.strip():
            raise InterfaceError(
                f"interface {key} must be a nonempty string without surrounding whitespace"
            )
    return {"id": reference["id"], "version": reference["version"]}


def _interpret(spec: Any) -> Signature:
    spec = _shape(spec, {"id", "version", "callables", "types", "typing", "associated"}, "interface")
    reference = validate_interface_reference(
        {key: spec.get(key) for key in ("id", "version")}
    )
    callables = spec.get("callables", {})
    if not isinstance(callables, dict):
        raise InterfaceError(
            "interface callables must map export names to call specifications"
        )
    types = spec.get("types", [])
    if not isinstance(types, list):
        raise InterfaceError("interface types must be a list of export names")
    for name in types:
        _name(name, "type export")
    if len(set(types)) != len(types):
        raise InterfaceError("interface type exports must be distinct")

    parsed = {}
    for export, declaration in callables.items():
        _name(export, "callable export")
        declaration = _shape(
            declaration, {"parameters", "asynchronous"}, f"callable {export}"
        )
        asynchronous = declaration.get("asynchronous", False)
        if type(asynchronous) is not bool:
            raise InterfaceError(f"{export}.asynchronous must be a boolean")
        parameters = declaration.get("parameters", [])
        if not isinstance(parameters, list):
            raise InterfaceError(f"{export}.parameters must be a list")
        converted = []
        variadic_kinds = set()
        for position, parameter in enumerate(parameters):
            label = f"{export}.parameters[{position}]"
            parameter = _shape(parameter, {"name", "kind", "required"}, label)
            name = _name(parameter.get("name"), f"{label}.name")
            kind_name = parameter.get("kind")
            if not isinstance(kind_name, str) or kind_name not in _KINDS:
                raise InterfaceError(
                    f"{label}.kind must be an inspect.Parameter kind name"
                )
            required = parameter.get("required")
            if type(required) is not bool:
                raise InterfaceError(f"{label}.required must be a boolean")
            kind = _KINDS[kind_name]
            variadic = kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
            if variadic and required:
                raise InterfaceError(f"{label}: variadic parameters cannot be required")
            if variadic and kind in variadic_kinds:
                raise InterfaceError(
                    f"{label}: a call shape can contain only one {kind.name} parameter"
                )
            if variadic:
                variadic_kinds.add(kind)
            default = inspect.Parameter.empty if required or variadic else None
            converted.append(inspect.Parameter(name, kind, default=default))
        try:
            shape = inspect.Signature(converted)
        except ValueError as error:
            raise InterfaceError(
                f"{export}: invalid parameter sequence: {error}"
            ) from error
        parsed[export] = CallableSpec(shape, asynchronous=asynchronous)
    try:
        return Signature(
            reference["id"], reference["version"], parsed, types=tuple(sorted(types))
        )
    except ContractError as error:
        raise InterfaceError(str(error)) from error


def _associated(spec: Any) -> dict:
    """Validate abstract type parameters and first-order type constructors."""
    spec = _shape(spec, {"types", "constructors"}, "interface associated")
    kinds = {"identity", "shared", "affine", "linear"}
    types = spec.get("types", {})
    constructors = spec.get("constructors", {})
    if not isinstance(types, dict) or not isinstance(constructors, dict):
        raise InterfaceError("associated types and constructors must be objects")
    for name, kind in types.items():
        _name(name, "associated type")
        if not isinstance(kind, str) or kind not in kinds:
            raise InterfaceError(f"associated type {name} has invalid kind")
    normalized = {}
    for name, declaration in constructors.items():
        if not isinstance(name, str) or not name or name != name.strip():
            raise InterfaceError("associated constructor must have a nonempty trimmed name")
        declaration = _shape(declaration, {"parameters", "result"}, f"constructor {name}")
        parameters = declaration.get("parameters")
        result = declaration.get("result")
        if not isinstance(parameters, list) or any(
            not isinstance(kind, str) or kind not in kinds for kind in parameters
        ):
            raise InterfaceError(f"constructor {name} parameters must list kinds")
        if not isinstance(result, str) or result not in kinds:
            raise InterfaceError(f"constructor {name} result must be a kind")
        normalized[name] = {"parameters": list(parameters), "result": result}
    return {"types": dict(sorted(types.items())), "constructors": dict(sorted(normalized.items()))}


def _local_term(term: Any, associated: dict) -> dict:
    from .type_terms import normalize_term

    try:
        normalized = normalize_term(term, constructors=associated["constructors"])
    except ValueError as error:
        raise InterfaceError(str(error)) from error

    def check(value):
        if "from" in value:
            raise InterfaceError("interface type terms cannot reference dependency slots")
        if "var" in value:
            name = value["var"]
            if name not in associated["types"]:
                raise InterfaceError(f"undeclared associated type variable {name}")
            if value["kind"] != associated["types"][name]:
                raise InterfaceError(f"associated type variable {name} has inconsistent kind")
        for argument in value.get("args", []):
            check(argument)

    check(normalized)
    return normalized


def _typing(spec: dict, signature: Signature, associated: dict | None = None) -> dict:
    """Validate the first-order typed language without importing Python code."""
    associated = associated or {"types": {}, "constructors": {}}
    declaration = _shape(spec, {"types", "operations"}, "interface typing")
    types = declaration.get("types")
    operations = declaration.get("operations")
    if not isinstance(types, dict) or not isinstance(operations, dict):
        raise InterfaceError("typing requires types and operations objects")
    normalized_types = {}
    for name in types:
        _name(name, "typed local type")
    for name, value in sorted(types.items()):
        value = _shape(value, {"id", "term", "usage", "representation"}, f"typing.types.{name}")
        if ("id" in value) == ("term" in value):
            raise InterfaceError(f"typed type {name} requires exactly one of id or term")
        if "id" in value:
            identity = value["id"]
            if not isinstance(identity, str) or not identity or identity != identity.strip():
                raise InterfaceError(f"typed type {name}.id must be a nonempty trimmed string")
            identity_fields = {"id": identity}
        else:
            identity_fields = {"term": _local_term(value["term"], associated)}
        usage = value.get("usage")
        representation = value.get("representation")
        if not isinstance(usage, str) or usage not in {"shared", "affine", "linear"}:
            raise InterfaceError(f"typed type {name}.usage must be shared, affine, or linear")
        if not isinstance(representation, str) or representation not in {
            "opaque", "str", "int", "float", "bool", "bytes"
        }:
            raise InterfaceError(f"typed type {name}.representation is unsupported")
        if "term" in identity_fields and identity_fields["term"]["kind"] != usage:
            raise InterfaceError(f"typed type {name} term kind must equal its value usage")
        normalized_types[name] = {
            **identity_fields, "usage": usage, "representation": representation
        }
    identities = {}
    for value in normalized_types.values():
        # A nominal term and the legacy spelling of that same nominal identity
        # denote one type; constructor applications remain structurally distinct.
        term = value.get("term", {"nominal": value.get("id"), "kind": value["usage"]})
        identity = json.dumps(term, sort_keys=True, separators=(",", ":"))
        if "nominal" in term:
            identity = "nominal:" + term["nominal"]
        properties = (value["usage"], value["representation"])
        if identity in identities and identities[identity] != properties:
            raise InterfaceError(f"typed identity {identity} has inconsistent declarations")
        identities[identity] = properties
    if set(operations) != set(signature.callables):
        raise InterfaceError("typed operations must match all callable exports exactly")
    normalized_operations = {}
    for name, callable_spec in signature.callables.items():
        value = _shape(
            operations[name], {"parameters", "returns", "effects"}, f"typing.operations.{name}"
        )
        if callable_spec.asynchronous:
            raise InterfaceError(f"typed operation {name} must be synchronous")
        parameters = value.get("parameters")
        shape = callable_spec.signature.parameters
        if not isinstance(parameters, dict) or set(parameters) != set(shape):
            raise InterfaceError(f"typed operation {name} parameters must match its call shape")
        normalized_parameters = {}
        for parameter_name, parameter in shape.items():
            if parameter.kind not in {
                inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD
            } or parameter.default is not inspect.Parameter.empty:
                raise InterfaceError(
                    f"typed operation {name} supports only required positional parameters"
                )
            binding = _shape(
                parameters[parameter_name], {"type", "mode"},
                f"typing.operations.{name}.parameters.{parameter_name}"
            )
            local_type = binding.get("type")
            if not isinstance(local_type, str) or local_type not in normalized_types:
                raise InterfaceError(f"typed parameter {name}.{parameter_name} has unknown type")
            mode = binding.get("mode")
            usage = normalized_types[local_type]["usage"]
            allowed = {"share"} if usage == "shared" else {"move", "borrow"}
            if not isinstance(mode, str) or mode not in allowed:
                raise InterfaceError(f"typed parameter {name}.{parameter_name} has invalid mode for {usage}")
            normalized_parameters[parameter_name] = {"type": local_type, "mode": mode}
        returns = value.get("returns")
        if not isinstance(returns, list) or any(
            not isinstance(item, str) or item not in normalized_types for item in returns
        ):
            raise InterfaceError(f"typed operation {name}.returns must list declared local types")
        effects = value.get("effects")
        if not isinstance(effects, list) or any(
            not isinstance(item, str) or not item or item != item.strip() for item in effects
        ):
            raise InterfaceError(f"typed operation {name}.effects must list nonempty trimmed strings")
        if len(set(effects)) != len(effects):
            raise InterfaceError(f"typed operation {name}.effects must be distinct")
        normalized_operations[name] = {
            "parameters": normalized_parameters, "returns": list(returns), "effects": sorted(effects)
        }
    return {"types": normalized_types, "operations": normalized_operations}


def validate_interface(spec: dict) -> dict:
    """Return a defensive, canonical JSON declaration or raise InterfaceError.

    Parameter order is significant. Optional defaults are represented only by
    ``required = false``; their values are deliberately outside this schema.
    """
    signature = _interpret(spec)
    result = signature.metadata()
    associated = _associated(spec["associated"]) if "associated" in spec else None
    if associated is not None:
        result["associated"] = associated
    if "typing" in spec:
        result["typing"] = _typing(spec["typing"], signature, associated)
    return result


def signature_from_spec(spec: dict) -> Signature:
    """Validate all metadata and construct the runtime call-shape Signature.

    Typed metadata remains in ``validate_interface`` output for the composition
    compiler; the runtime Signature itself does not enforce ownership or effects.
    """
    signature = _interpret(spec)
    associated = _associated(spec["associated"]) if "associated" in spec else None
    if "typing" in spec:
        _typing(spec["typing"], signature, associated)
    return signature


__all__ = [
    "InterfaceError",
    "validate_interface",
    "validate_interface_reference",
    "signature_from_spec",
]
