"""Graph projection cannot strengthen untyped or incompatible contracts."""

from copy import deepcopy

import pytest
from test_assemblies import publish

from module_families.module_build import resolve_module
from module_families.registry import Registry


def interface(identifier, *, alias="Resource", mode="move", effects=("write",)):
    return {
        "id": identifier, "version": "1", "types": [],
        "callables": {"run": {"parameters": [
            {"name": "resource", "kind": "POSITIONAL_OR_KEYWORD", "required": True}
        ]}},
        "typing": {
            "types": {
                alias: {"id": "example.resource", "usage": "linear", "representation": "opaque"},
                "Text": {"id": "python.str", "usage": "shared", "representation": "str"},
            },
            "operations": {"run": {
                "parameters": {"resource": {"type": alias, "mode": mode}},
                "returns": ["Text"], "effects": list(effects),
            }},
        },
    }


def resolve(tmp_path, source, result):
    repository = Registry(tmp_path / "repository")
    publish(tmp_path, repository, "contracts", interfaces=[source, result])
    publish(tmp_path, repository, "provider", members=[{
        "id": "run", "version": "1.0.0", "kind": "algorithm",
        "symbol": "provider:run", "summary": "Projection test provider",
        "provides": {"id": source["id"], "version": "1"}, "effects": ["write"],
    }], source='def run(resource):\n    return "done"\n')
    document = {
        "schema_version": 1,
        "family": {"name": "graph", "version": "1.0.0", "description": "Typed projection"},
        "publisher": {"name": "tests"},
        "module": {"id": "projected", "version": "1.0.0", "provides": {"id": result["id"], "version": "1"}},
        "nodes": {"provider": {"select": {"family": "provider", "member": "tests.run"}}},
        "exports": {"run": "provider.run"},
    }
    return resolve_module(document, repository)


def test_untyped_source_cannot_claim_typed_public_contract(tmp_path):
    source = interface("example.source")
    del source["typing"]
    result = resolve(tmp_path, source, interface("example.result"))
    assert result["status"] == "unsatisfied"
    assert "requires a typed source" in str(result)


def test_local_type_alias_names_do_not_change_nominal_contract(tmp_path):
    result = resolve(tmp_path, interface("example.source", alias="Transaction"), interface("example.result", alias="Session"))
    assert result["status"] == "unique"


@pytest.mark.parametrize("source_mode,result_mode", [("move", "borrow"), ("borrow", "move")])
def test_ownership_modes_cannot_change_through_export(tmp_path, source_mode, result_mode):
    result = resolve(tmp_path, interface("example.source", mode=source_mode), interface("example.result", mode=result_mode))
    assert result["status"] == "unsatisfied"
    assert "typed export contract mismatch" in str(result)


def test_source_effects_cannot_exceed_public_operation_bound(tmp_path):
    result = resolve(tmp_path, interface("example.source", effects=("write", "network")), interface("example.result"))
    assert result["status"] == "unsatisfied"
    assert "typed export effects exceed" in str(result)


def test_public_contract_can_allow_more_effects_than_source_uses(tmp_path):
    result = resolve(tmp_path, interface("example.source"), interface("example.result", effects=("write", "network")))
    assert result["status"] == "unique"


@pytest.mark.parametrize("field,value", [("id", "other.resource"), ("usage", "affine"), ("representation", "str")])
def test_nominal_type_properties_are_preserved(tmp_path, field, value):
    source = interface("example.source")
    result = interface("example.result")
    result["typing"]["types"]["Resource"][field] = value
    report = resolve(tmp_path, source, result)
    assert report["status"] == "unsatisfied"
    assert "typed export contract mismatch" in str(report)


def test_return_types_and_arity_are_preserved(tmp_path):
    source = interface("example.source")
    result = interface("example.result")
    result["typing"]["operations"]["run"]["returns"] = ["Text", "Text"]
    report = resolve(tmp_path, source, result)
    assert report["status"] == "unsatisfied"
    assert "typed export contract mismatch" in str(report)


def test_untyped_projection_can_explicitly_erase_typed_metadata(tmp_path):
    source = interface("example.source")
    result = deepcopy(source)
    result["id"] = "example.result"
    del result["typing"]
    assert resolve(tmp_path, source, result)["status"] == "unique"
