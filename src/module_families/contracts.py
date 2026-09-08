"""Explicit runtime module interfaces and checked module composition.

These checks cover call acceptance and nominal Python type identity. They do not
prove behavioral substitution or restrict execution of arbitrary Python code.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import keyword
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import uuid4


class ContractError(ValueError):
    """A declaration, module export, or dependency binding is incompatible."""


def _name(value: str, what: str) -> None:
    if (
        not isinstance(value, str)
        or not value.isidentifier()
        or keyword.iskeyword(value)
    ):
        raise ContractError(f"{what} must be a Python identifier: {value!r}")


def _text(value: str, what: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ContractError(
            f"{what} must be a nonempty string without surrounding whitespace"
        )


def _async_kind(value: Callable[..., Any]) -> str:
    method = getattr(type(value), "__call__", None)  # noqa: B004 - inspect its async behavior, not callability.
    if inspect.isasyncgenfunction(value) or inspect.isasyncgenfunction(method):
        return "async-generator"
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(method):
        return "async"
    return "sync"


def _inspect(value: Any, label: str) -> inspect.Signature:
    if not callable(value):
        raise ContractError(f"{label} must be callable")
    try:
        return inspect.signature(value, follow_wrapped=False, eval_str=False)
    except (TypeError, ValueError) as exc:
        raise ContractError(
            f"{label} has no inspectable Python call signature"
        ) from exc


@dataclass(frozen=True)
class CallableSpec:
    """The calls an export must accept; annotations are not runtime type checks."""

    signature: inspect.Signature
    asynchronous: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.signature, inspect.Signature):
            raise ContractError("CallableSpec requires an inspect.Signature")
        if not isinstance(self.asynchronous, bool):
            raise ContractError("asynchronous must be a boolean")

    @classmethod
    def from_callable(cls, prototype: Callable[..., Any]) -> CallableSpec:
        signature = _inspect(prototype, "contract prototype")
        kind = _async_kind(prototype)
        if kind == "async-generator":
            raise ContractError("async generator contracts are not supported")
        return cls(signature, asynchronous=kind == "async")

    def metadata(self) -> dict[str, Any]:
        # Defaults/annotations may contain arbitrary objects. Avoid evaluating or
        # serializing them: only argument acceptance is part of this contract.
        return {
            "asynchronous": self.asynchronous,
            "parameters": [
                {
                    "name": parameter.name,
                    "kind": parameter.kind.name,
                    "required": parameter.default is inspect.Parameter.empty
                    and parameter.kind
                    not in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    ),
                }
                for parameter in self.signature.parameters.values()
            ],
        }

    def check(self, candidate: Any, label: str = "export") -> None:
        actual = _inspect(candidate, label)
        expected_kind = "async" if self.asynchronous else "sync"
        if _async_kind(candidate) != expected_kind:
            raise ContractError(
                f"{label} must be {expected_kind}, got {_async_kind(candidate)}"
            )
        _check_call_acceptance(self.signature, actual, label)


def _check_call_acceptance(
    expected: inspect.Signature, actual: inspect.Signature, label: str
) -> None:
    """Check inclusion of the ordinary Python argument-binding languages.

    For each positional prefix, required keywords form a base call. Each optional
    keyword is checked independently: duplicate/unknown keyword failures concern
    one name at a time, while missing required arguments fail on the base call.
    Variadics require matching variadic capacity, with extra keyword probes for
    names that could collide with the provider's positional parameters.
    """
    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    expected_params = tuple(expected.parameters.values())
    actual_params = tuple(actual.parameters.values())
    expected_pos = tuple(p for p in expected_params if p.kind in positional_kinds)
    actual_pos = tuple(p for p in actual_params if p.kind in positional_kinds)
    varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in expected_params)
    varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in expected_params)
    if varargs and not any(
        p.kind == inspect.Parameter.VAR_POSITIONAL for p in actual_params
    ):
        raise ContractError(
            f"{label} must accept arbitrary positional arguments (*args)"
        )
    if varkw and not any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in actual_params
    ):
        raise ContractError(
            f"{label} must accept arbitrary keyword arguments (**kwargs)"
        )

    max_positional = len(expected_pos)
    if varargs:
        max_positional = max(max_positional, len(actual_pos)) + 1
    marker = object()
    fresh_keyword = "__contract_extra_keyword__"
    known_names = set(expected.parameters) | set(actual.parameters)
    while fresh_keyword in known_names:
        fresh_keyword += "_"

    for count in range(max_positional + 1):
        occupied = {p.name for p in expected_pos[:count]}
        required: dict[str, object] = {}
        optional: set[str] = set()
        for parameter in expected_params:
            if (
                parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
                and parameter.name not in occupied
            ):
                if parameter.default is inspect.Parameter.empty:
                    required[parameter.name] = marker
                else:
                    optional.add(parameter.name)
        if varkw:
            optional.update(known_names - set(required))
            optional.add(fresh_keyword)
        positional = (marker,) * count
        calls = [required]
        calls.extend({**required, name: marker} for name in sorted(optional))
        for keywords in calls:
            try:
                _bind(expected, positional, keywords)
            except TypeError:
                # This positional prefix or extra keyword is outside the contract.
                continue
            try:
                _bind(actual, positional, keywords)
            except TypeError as exc:
                keys = ", ".join(sorted(keywords)) or "none"
                raise ContractError(
                    f"{label} rejects a contract call with {count} positional arguments "
                    f"and keywords [{keys}]: {exc}"
                ) from exc


def _bind(
    signature: inspect.Signature, positional: tuple, keywords: dict
) -> inspect.BoundArguments:
    # Some Python versions' inspect.bind accept a later optional positional-only
    # parameter by keyword after an earlier optional parameter is omitted. Real
    # Python calls reject this, so enforce this rule independently of inspect.
    parameters = tuple(signature.parameters.values())
    if not any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    ):
        forbidden = [
            parameter.name
            for parameter in parameters
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY
            and parameter.name in keywords
        ]
        if forbidden:
            raise TypeError(
                f"positional-only parameters passed as keywords: {', '.join(forbidden)}"
            )
    return signature.bind(*positional, **keywords)


@dataclass(frozen=True)
class Signature:
    """A versioned contract with callable exports and nominal type exports."""

    id: str
    version: str
    callables: Mapping[str, CallableSpec | Callable[..., Any]] = field(
        default_factory=dict
    )
    types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "contract id")
        _text(self.version, "contract version")
        if not isinstance(self.callables, Mapping):
            raise ContractError(
                "callables must be a mapping of export names to prototypes"
            )
        if not isinstance(self.types, (tuple, list)):
            raise ContractError("types must be a sequence of export names")
        specs = {}
        for name, spec in self.callables.items():
            _name(name, "callable export")
            specs[name] = (
                spec
                if isinstance(spec, CallableSpec)
                else CallableSpec.from_callable(spec)
            )
        names = tuple(self.types)
        for name in names:
            _name(name, "type export")
        if len(set(names)) != len(names) or set(names) & set(specs):
            raise ContractError(
                "type exports must be distinct from each other and callable exports"
            )
        object.__setattr__(self, "callables", MappingProxyType(specs))
        object.__setattr__(self, "types", names)

    def reference(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version}

    def metadata(self) -> dict[str, Any]:
        return {
            **self.reference(),
            "callables": {
                name: spec.metadata() for name, spec in sorted(self.callables.items())
            },
            "types": sorted(self.types),
        }

    def check(self, exports: Mapping[str, Any]) -> None:
        if not isinstance(exports, Mapping):
            raise ContractError(f"{self.id}@{self.version} exports must be a mapping")
        for name, spec in self.callables.items():
            if name not in exports:
                raise ContractError(
                    f"{self.id}@{self.version} is missing callable export {name!r}"
                )
            spec.check(exports[name], f"{self.id}.{name}")
        for name in self.types:
            if name not in exports:
                raise ContractError(
                    f"{self.id}@{self.version} is missing type export {name!r}"
                )
            if not isinstance(exports[name], type):
                raise ContractError(
                    f"{self.id}.{name} must export a Python type object"
                )

    def seal(
        self,
        exports: Mapping[str, Any],
        *,
        provides: Mapping[str, str] | None = None,
        identity: str | None = None,
        indices: Mapping[str, str] | None = None,
    ) -> ModuleView:
        """Validate exports and expose only declared names through a read-only view.

        `provides` is the provider's declared ID/version. Omit it when directly
        constructing a provider under this signature. Explicit identities are
        caller-managed logical names; they do not attest to the exported bytes.
        """
        if provides is not None:
            if not isinstance(provides, Mapping):
                raise ContractError(
                    "provider contract reference must be a mapping with id and version"
                )
            if dict(provides) != self.reference():
                raise ContractError(
                    f"provider declares {dict(provides)!r}; required {self.reference()!r}"
                )
        if identity is None:
            identity = f"local:{uuid4().hex}"
        return ModuleView(exports, self, identity, indices=indices)


class ModuleView(Mapping[str, Any]):
    """A read-only export projection, not a sandbox or a deep freeze."""

    __slots__ = ("_exports", "_signature", "_identity", "_instance_id", "_indices")

    def __init__(self, exports: Mapping[str, Any], signature: Signature, identity: str, *, indices=None):
        # Public construction checks too, so callers cannot accidentally bypass
        # conformance by skipping Signature.seal(). Requirement rechecks on bind.
        if not isinstance(signature, Signature):
            raise ContractError("module view requires a Signature")
        signature.check(exports)
        _text(identity, "module identity")
        selected = {
            name: exports[name] for name in (*signature.callables, *signature.types)
        }
        object.__setattr__(self, "_exports", MappingProxyType(selected))
        object.__setattr__(self, "_signature", signature)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_instance_id", uuid4().hex)
        if indices is not None and (not isinstance(indices, Mapping) or any(
            not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str) or not v
            for k, v in indices.items()
        )):
            raise ContractError("indices must map identifiers to nonempty identities")
        object.__setattr__(self, "_indices", MappingProxyType(dict(indices or {})))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("module views are read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("module views are read-only")

    def __getitem__(self, name: str) -> Any:
        return self._exports[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._exports)

    def __len__(self) -> int:
        return len(self._exports)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._exports[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def signature(self) -> Signature:
        return self._signature

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def provides(self) -> dict[str, str]:
        return self.signature.reference()

    def metadata(self) -> dict[str, Any]:
        return {
            "provides": self.provides,
            "identity": self.identity,
            "instance_id": self.instance_id,
            "indices": dict(self._indices),
        }


@dataclass(frozen=True)
class Requirement:
    """An exact contract ID/version plus optional fixed nominal type bindings."""

    signature: Signature
    types: Mapping[str, type] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.signature, Signature):
            raise ContractError("requirement signature must be a Signature")
        if not isinstance(self.types, Mapping):
            raise ContractError("requirement types must be a mapping")
        for name, value in self.types.items():
            if name not in self.signature.types:
                raise ContractError(
                    f"{name!r} is not a declared type in {self.signature.id}"
                )
            if not isinstance(value, type):
                raise ContractError(f"required {name!r} must be a Python type object")
        object.__setattr__(self, "types", MappingProxyType(dict(self.types)))

    def metadata(self) -> dict[str, Any]:
        # Names document fixed obligations; live Python type identity cannot be
        # encoded faithfully as a JSON string and is checked at runtime.
        return {
            "requires": self.signature.reference(),
            "fixed_types": sorted(self.types),
        }

    def check(self, module: ModuleView, slot: str = "module") -> None:
        if not isinstance(module, ModuleView):
            raise ContractError(f"slot {slot!r} requires a sealed ModuleView")
        if module.provides != self.signature.reference():
            raise ContractError(
                f"slot {slot!r} provides {module.provides!r}; "
                f"requires {self.signature.reference()!r}"
            )
        self.signature.check(module)
        for name, expected in self.types.items():
            if module[name] is not expected:
                raise ContractError(
                    f"slot {slot!r} has incompatible nominal type {name!r}"
                )


@dataclass(frozen=True)
class Functor:
    """Check named module dependencies before invoking a synchronous factory.

    Every call invokes the factory. Equal logical bindings give equal composition
    identities, while every result has a fresh instance_id. There is no object
    cache and no automatic creation of fresh nominal types.
    """

    name: str
    parameters: Mapping[str, Requirement]
    result: Signature
    factory: Callable[..., Mapping[str, Any]]
    sharing: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "factory name")
        if not isinstance(self.parameters, Mapping):
            raise ContractError("factory parameters must be a mapping")
        parameters = dict(self.parameters)
        for name, requirement in parameters.items():
            _name(name, "dependency slot")
            if not isinstance(requirement, Requirement):
                raise ContractError(f"slot {name!r} must declare a Requirement")
        if not isinstance(self.result, Signature):
            raise ContractError("factory result must declare a Signature")
        factory_signature = _inspect(self.factory, f"factory {self.name}")
        if _async_kind(self.factory) != "sync":
            raise ContractError("module factories must be synchronous")
        try:
            _bind(factory_signature, (), {name: object() for name in parameters})
        except TypeError as exc:
            raise ContractError(
                f"factory {self.name} cannot accept its dependency slots: {exc}"
            ) from exc
        if not isinstance(self.sharing, (tuple, list)):
            raise ContractError(
                "sharing must be a sequence of pairs of slot.Type paths"
            )
        sharing = []
        for pair in self.sharing:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ContractError(
                    "each sharing constraint must contain two slot.Type paths"
                )
            for path in pair:
                if not isinstance(path, str) or path.count(".") != 1:
                    raise ContractError(f"invalid shared type path: {path!r}")
                slot, name = path.split(".")
                if (
                    slot not in parameters
                    or name not in parameters[slot].signature.types
                ):
                    raise ContractError(
                        f"shared type path {path!r} is not a declared dependency type"
                    )
            sharing.append(tuple(pair))
        object.__setattr__(self, "parameters", MappingProxyType(parameters))
        object.__setattr__(self, "sharing", tuple(sharing))

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": {
                name: value.metadata()
                for name, value in sorted(self.parameters.items())
            },
            "result": self.result.reference(),
            "sharing": [list(pair) for pair in self.sharing],
        }

    def __call__(self, **bindings: ModuleView) -> ModuleView:
        missing = sorted(set(self.parameters) - set(bindings))
        extra = sorted(set(bindings) - set(self.parameters))
        if missing or extra:
            raise ContractError(
                f"factory {self.name} binding mismatch: missing={missing}, extra={extra}"
            )
        for slot, requirement in self.parameters.items():
            requirement.check(bindings[slot], slot)
        for left, right in self.sharing:
            left_slot, left_name = left.split(".")
            right_slot, right_name = right.split(".")
            if bindings[left_slot][left_name] is not bindings[right_slot][right_name]:
                raise ContractError(
                    f"type sharing conflict: {left} and {right} are different Python types"
                )
        identity_payload = {
            "factory": self.metadata(),
            "bindings": {
                name: module.identity for name, module in sorted(bindings.items())
            },
        }
        encoded = json.dumps(
            identity_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        identity = "binding:" + hashlib.sha256(encoded).hexdigest()
        exports = self.factory(**bindings)
        return self.result.seal(exports, identity=identity)


__all__ = [
    "CallableSpec",
    "ContractError",
    "Functor",
    "ModuleView",
    "Requirement",
    "Signature",
]
