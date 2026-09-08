"""Runtime ownership guards for the checked module language.

Adapters receive raw Python values and are trusted: these guards do not prevent an
adapter from retaining aliases, nor do they promise exactly-once external effects.
"""
from __future__ import annotations

from contextlib import ExitStack
from threading import RLock
from typing import Any, Callable, Mapping, Sequence


class OwnershipError(ValueError):
    """A checked call violated its value or resource contract."""


class Owned:
    """An opaque, movable owner. Access to its payload requires a checked call."""

    __slots__ = ("_value", "_type_id", "_usage", "_valid", "_borrows", "_lock")

    def __init__(self, value: Any, type_id: str, usage: str = "linear"):
        if not isinstance(type_id, str) or not type_id:
            raise OwnershipError("An owner requires a nonempty nominal type id")
        if usage not in {"linear", "affine"}:
            raise OwnershipError("Owners require linear or affine usage")
        if isinstance(value, Owned):
            raise OwnershipError("An owner cannot wrap another owner")
        self._value = value
        self._type_id = type_id
        self._usage = usage
        self._valid = True
        self._borrows = 0
        self._lock = RLock()

    @property
    def type_id(self) -> str:
        return self._type_id

    @property
    def usage(self) -> str:
        return self._usage

    @property
    def valid(self) -> bool:
        with self._lock:
            return self._valid

    def __copy__(self):
        raise OwnershipError("Owners cannot be copied; use move_owned")

    def __deepcopy__(self, memo):
        raise OwnershipError("Owners cannot be copied; use move_owned")

    def __reduce_ex__(self, protocol):
        raise OwnershipError("Owners cannot be serialized")


def move_owned(handle: Owned) -> Owned:
    """Transfer an ownership handle, invalidating the old handle atomically."""
    if not isinstance(handle, Owned):
        raise OwnershipError("Move requires an Owned value")
    with handle._lock:
        _live(handle)
        if handle._borrows:
            raise OwnershipError("Cannot move an actively borrowed owner")
        result = Owned(handle._value, handle.type_id, handle.usage)
        handle._valid = False
        handle._value = None
        return result


def drop_owned(handle: Owned) -> None:
    """Explicitly discard an affine owner without implying resource cleanup."""
    if not isinstance(handle, Owned):
        raise OwnershipError("Drop requires an Owned value")
    with handle._lock:
        _live(handle)
        if handle.usage != "affine":
            raise OwnershipError("Only affine owners may be dropped")
        if handle._borrows:
            raise OwnershipError("Cannot drop an actively borrowed owner")
        handle._valid = False
        handle._value = None


def _live(handle: Owned) -> None:
    if not handle._valid:
        raise OwnershipError("Owner has already been moved")


_REPRESENTATIONS = {
    "int": int, "float": float, "str": str, "bool": bool, "bytes": bytes,
}


def _spec(spec: Mapping[str, Any], *, parameter: bool) -> None:
    if not isinstance(spec.get("id"), str) or not spec["id"]:
        raise OwnershipError("A resolved type requires a nominal id")
    if spec.get("usage") not in {"shared", "affine", "linear"}:
        raise OwnershipError("Unknown type usage")
    representation = spec.get("representation")
    if representation != "opaque" and representation not in _REPRESENTATIONS:
        raise OwnershipError(f"Unknown representation: {representation!r}")
    if parameter:
        expected = {"share"} if spec["usage"] == "shared" else {"move", "borrow"}
        if spec.get("mode") not in expected:
            raise OwnershipError("Parameter mode is incompatible with type usage")


def _representation(value: Any, spec: Mapping[str, Any]) -> None:
    if isinstance(value, Owned):
        raise OwnershipError("Raw adapters must not return or embed Owned handles")
    representation = spec["representation"]
    if representation != "opaque" and type(value) is not _REPRESENTATIONS[representation]:
        raise OwnershipError(f"Expected {representation} representation for {spec['id']}")


def _check_result_aliases(value: Any, borrowed: set[int], seen: set[int]) -> None:
    if isinstance(value, Owned):
        raise OwnershipError("Raw adapters must not return Owned handles")
    if id(value) in borrowed:
        raise OwnershipError("Borrowed values cannot escape through return values")
    if id(value) in seen:
        return
    seen.add(id(value))
    if type(value) is dict:
        children = (*value.keys(), *value.values())
    elif type(value) in {list, tuple, set, frozenset}:
        children = value
    else:
        return  # Arbitrary object interiors are the trusted adapter boundary.
    for child in children:
        _check_result_aliases(child, borrowed, seen)


_CHECKED_SIGNATURE = "__module_families_checked_signature__"


def _signature(parameter_specs, returns):
    parameters = tuple(
        tuple(spec[key] for key in ("id", "usage", "representation", "mode"))
        for spec in parameter_specs
    )
    outputs = tuple(
        tuple(spec[key] for key in ("id", "usage", "representation"))
        for spec in returns
    )
    return parameters, outputs


def mark_checked(function, parameter_specs, returns):
    """Mark compiler-generated code without wrapping or changing its signature.

    The generated function must transfer its own inputs atomically on entry and
    return checked values. This marker is a trusted compiler convention, not an
    authentication or sandbox boundary against arbitrary Python code.
    """
    for spec in parameter_specs:
        _spec(spec, parameter=True)
        if spec["mode"] == "borrow":
            raise OwnershipError("Checked public functions cannot accept borrow parameters")
    for spec in returns:
        _spec(spec, parameter=False)
    if not callable(function):
        raise OwnershipError("A checked marker requires a callable")
    setattr(function, _CHECKED_SIGNATURE, _signature(parameter_specs, returns))
    return function


def _return_values(result, returns):
    if not returns:
        if result is not None:
            raise OwnershipError("A zero-return adapter must return None")
        return ()
    if len(returns) == 1:
        return (result,)
    if type(result) is tuple and len(result) == len(returns):
        return result
    raise OwnershipError("Multiple returns require an exact-arity tuple")


def _invoke_checked(function, arguments, parameter_specs, returns):
    if getattr(function, _CHECKED_SIGNATURE) != _signature(parameter_specs, returns):
        raise OwnershipError("Checked function signature does not match requested contract")
    # Generated entry code performs the input transfer. Unboxing here would
    # consume the caller's handles twice and break nested checked composition.
    values = _return_values(function(*arguments), returns)
    handles = {id(value): value for value in values if isinstance(value, Owned)}
    with ExitStack() as stack:
        for identity in sorted(handles):
            stack.enter_context(handles[identity]._lock)
        seen = set()
        for value, spec in zip(values, returns, strict=True):
            if spec["usage"] == "shared":
                _representation(value, spec)
                _check_result_aliases(value, set(), set())
                continue
            if not isinstance(value, Owned):
                raise OwnershipError("Checked resource returns require Owned values")
            _live(value)
            if value.type_id != spec["id"] or value.usage != spec["usage"]:
                raise OwnershipError("Checked return nominal type or usage does not match")
            _representation(value._value, spec)
            if id(value) in seen:
                raise OwnershipError("Checked returns cannot duplicate an owned resource")
            seen.add(id(value))
    return values


def invoke(
    function: Callable[..., Any],
    arguments: Sequence[Any],
    parameter_specs: Sequence[Mapping[str, Any]],
    returns: Sequence[Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Preflight and execute a trusted adapter, consuming moves before execution.

    Borrow locks cover the entire call. Failure never resurrects consumed owners.
    Return validation is all-or-nothing before new output owners are constructed.
    """
    if len(arguments) != len(parameter_specs):
        raise OwnershipError("Argument arity does not match parameter contract")
    for spec in parameter_specs:
        _spec(spec, parameter=True)
    for spec in returns:
        _spec(spec, parameter=False)
    if hasattr(function, _CHECKED_SIGNATURE):
        return _invoke_checked(function, arguments, parameter_specs, returns)
    handles = {id(arg): arg for arg in arguments if isinstance(arg, Owned)}
    with ExitStack() as stack:
        for identity in sorted(handles):
            stack.enter_context(handles[identity]._lock)
        raw = []
        modes: dict[int, list[str]] = {}
        for arg, spec in zip(arguments, parameter_specs, strict=True):
            if spec["usage"] == "shared":
                _representation(arg, spec)
                raw.append(arg)
                continue
            if not isinstance(arg, Owned):
                raise OwnershipError("Linear and affine parameters require Owned values")
            _live(arg)
            if arg.type_id != spec["id"] or arg.usage != spec["usage"]:
                raise OwnershipError("Owner nominal type or usage does not match parameter")
            mode = spec["mode"]
            if mode == "move" and arg._borrows:
                raise OwnershipError("Cannot move an actively borrowed owner")
            modes.setdefault(id(arg), []).append(mode)
            _representation(arg._value, spec)
            raw.append(arg._value)
        for usages in modes.values():
            if "move" in usages and len(usages) != 1:
                raise OwnershipError("Aliased move or move-and-borrow arguments")
        borrowed = set()
        for identity, usages in modes.items():
            handle = handles[identity]
            if usages[0] == "move":
                handle._valid = False
                handle._value = None
            else:
                borrowed.add(id(handle._value))
                handle._borrows += 1
                stack.callback(_release_borrow, handle)
        result = function(*raw)
        values = _return_values(result, returns)
        for value, spec in zip(values, returns, strict=True):
            _representation(value, spec)
            _check_result_aliases(value, borrowed, set())
        owned_outputs = [id(value) for value, spec in zip(values, returns, strict=True)
                         if spec["usage"] != "shared"]
        for identity in owned_outputs:
            if sum(id(value) == identity for value in values) != 1:
                raise OwnershipError("Return values cannot duplicate an owned resource")
        return tuple(
            value if spec["usage"] == "shared" else Owned(value, spec["id"], spec["usage"])
            for value, spec in zip(values, returns, strict=True)
        )


def _release_borrow(handle: Owned) -> None:
    handle._borrows -= 1
