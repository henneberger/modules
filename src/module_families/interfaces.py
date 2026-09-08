"""Serializable interface declarations, interpreted without eval or imports.

Interfaces describe accepted call shapes and named nominal type exports. They
do not deserialize Python types, evaluate annotations or defaults, import an
implementation, or prove returned values and behavioral laws.
"""

from __future__ import annotations

import inspect
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
    spec = _shape(spec, {"id", "version", "callables", "types"}, "interface")
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


def validate_interface(spec: dict) -> dict:
    """Return a defensive, canonical JSON declaration or raise InterfaceError.

    Parameter order is significant. Optional defaults are represented only by
    ``required = false``; their values are deliberately outside this schema.
    """
    return _interpret(spec).metadata()


def signature_from_spec(spec: dict) -> Signature:
    """Construct a runtime Signature solely from validated declarative data."""
    return _interpret(spec)


__all__ = [
    "InterfaceError",
    "validate_interface",
    "validate_interface_reference",
    "signature_from_spec",
]
