from __future__ import annotations


def validate_mixin(card, parameters=None, result=None):
    declaration = card.get("mixin")
    if declaration is None:
        return
    if not isinstance(declaration, dict) or set(declaration) - {"base", "overrides", "adds", "preserves_types", "preserves_instances", "effects"}:
        raise ValueError("invalid mixin transformation")
    base = declaration.get("base")
    if not isinstance(base, str) or base not in card.get("requires", {}):
        raise ValueError("mixin base must be a declared dependency")
    for key in ("overrides", "adds", "preserves_types", "preserves_instances"):
        values = declaration.get(key, [])
        if not isinstance(values, (list, tuple)) or any(not isinstance(name, str) or not name.isidentifier() for name in values) or len(set(values)) != len(values):
            raise ValueError(f"invalid mixin {key}")
    effects = declaration.get("effects", {})
    if not isinstance(effects, dict) or set(effects) - (set(declaration.get("overrides", [])) | set(declaration.get("adds", []))):
        raise ValueError("mixin effect additions must name overridden or added operations")
    for values in effects.values():
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError("mixin effect additions must be lists of nonempty effect names")
    if set(declaration.get("overrides", [])) & set(declaration.get("adds", [])):
        raise ValueError("mixin overrides and additions must be disjoint")
    for name in declaration.get("preserves_types", []):
        term = card.get("associated", {}).get("types", {}).get(name, {})
        if term.get("from") != base + "." + name:
            raise ValueError(f"mixin must preserve its base associated type: {name}")
    for name in declaration.get("preserves_instances", []):
        if card.get("instance_exports", {}).get(name) != base + "." + name:
            raise ValueError(f"mixin must preserve its base instance role: {name}")
    if parameters is not None and result is not None:
        source = parameters[base].signature
        original = set(source.callables) | set(source.types)
        final = set(result.callables) | set(result.types)
        overrides, adds = set(declaration.get("overrides", [])), set(declaration.get("adds", []))
        if not overrides <= set(source.callables) or adds & original or final != original | adds:
            raise ValueError("mixin result must inherit its base and explicitly declare additions and overrides")
        from .contracts import _check_call_acceptance
        for name in source.callables:
            if name not in result.callables or source.callables[name].asynchronous != result.callables[name].asynchronous:
                raise ValueError(f"mixin changes operation kind: {name}")
            _check_call_acceptance(source.callables[name].signature, result.callables[name].signature, name)
        if not set(source.types) <= set(result.types):
            raise ValueError("mixin must preserve base type exports")


def apply_mixin(declaration, exports, dependencies):
    if declaration is None:
        return exports
    from collections.abc import Mapping
    if not isinstance(exports, Mapping):
        raise ValueError("mixin implementation must return an export mapping")
    base = dependencies[declaration["base"]]
    changed = set(declaration.get("overrides", [])) | set(declaration.get("adds", []))
    if not changed <= set(exports):
        raise ValueError("mixin implementation omits a declared override or addition")
    for name, value in exports.items():
        if name not in changed and (name not in base or value is not base[name]):
            raise ValueError(f"mixin changes an export without declaring an override: {name}")
    return {**dict(base), **dict(exports)}


def validate_mixin_interfaces(card, interfaces):
    declaration = card.get("mixin")
    if declaration is None:
        return
    from .contracts import Requirement
    from .interfaces import signature_from_spec
    from .refinement import refine_signature

    result_ref = card["provides"]
    result = interfaces[(result_ref["id"], result_ref["version"])]
    parameters = {slot: Requirement(signature_from_spec(interfaces[(ref["id"], ref["version"])])) for slot, ref in card["requires"].items()}
    validate_mixin(card, parameters, signature_from_spec(result))
    base_name = declaration["base"]
    base_ref = card["requires"][base_name]
    base = interfaces[(base_ref["id"], base_ref["version"])]
    from copy import deepcopy

    permitted = deepcopy(base)
    if declaration.get("effects") and "typing" not in result:
        raise ValueError("mixin effect transformations require typed operation signatures")
    for name, effects in declaration.get("effects", {}).items():
        if name in permitted.get("typing", {}).get("operations", {}):
            operation = permitted["typing"]["operations"][name]
            operation["effects"] = sorted(set(operation["effects"]) | set(effects))
    refine_signature(result, permitted)
    for name in base.get("associated", {}).get("types", {}):
        if card.get("associated", {}).get("types", {}).get(name, {}).get("from") != base_name + "." + name:
            raise ValueError(f"mixin must preserve inherited abstract type: {name}")
    for name in base.get("instances", []):
        if card.get("instance_exports", {}).get(name) != base_name + "." + name:
            raise ValueError(f"mixin must preserve inherited resource identity: {name}")
