"""Interface-scoped abstract types remain declarative and kind checked."""
from copy import deepcopy

import pytest

from module_families.interfaces import (
    InterfaceError,
    signature_from_spec,
    validate_interface,
)


def interface():
    return {
        "id": "kb.embedding", "version": "1",
        "associated": {
            "types": {"Space": "identity", "DocumentId": "shared"},
            "constructors": {"kb.Vector": {"parameters": ["identity"], "result": "shared"}},
        },
        "callables": {"embed": {"parameters": []}},
        "typing": {
            "types": {"Vector": {
                "term": {"apply": "kb.Vector", "args": [{"var": "Space", "kind": "identity"}], "kind": "shared"},
                "usage": "shared", "representation": "opaque",
            }},
            "operations": {"embed": {"parameters": {}, "returns": ["Vector"], "effects": []}},
        },
    }


def test_associated_interface_roundtrip_defensive_and_shape_interpretation():
    source = interface()
    normalized = validate_interface(source)
    assert validate_interface(normalized) == normalized
    assert signature_from_spec(source).callables.keys() == {"embed"}
    normalized["associated"]["constructors"]["kb.Vector"]["parameters"].append("shared")
    assert source["associated"]["constructors"]["kb.Vector"]["parameters"] == ["identity"]


@pytest.mark.parametrize("term", [
    {"var": "Unknown", "kind": "shared"},
    {"var": "Space", "kind": "shared"},
    {"from": "embedding.Space", "kind": "shared"},
    {"var": "Space", "kind": "identity"},
    {"apply": "unknown", "args": [], "kind": "shared"},
    {"apply": "kb.Vector", "args": [], "kind": "shared"},
    {"apply": "kb.Vector", "args": [{"var": "DocumentId", "kind": "shared"}], "kind": "shared"},
    {"apply": "kb.Vector", "args": [{"from": "port.Space", "kind": "identity"}], "kind": "shared"},
    {"apply": "kb.Vector", "args": [{"var": "Space", "kind": "identity"}], "kind": "linear"},
])
def test_invalid_terms_rejected_at_both_entrypoints(term):
    spec = interface()
    spec["typing"]["types"]["Vector"]["term"] = term
    for validate in (validate_interface, signature_from_spec):
        with pytest.raises(InterfaceError):
            validate(spec)


@pytest.mark.parametrize("associated", [
    None, [], {"unexpected": {}}, {"types": []}, {"constructors": []},
    {"types": {"bad name": "identity"}}, {"types": {"class": "shared"}},
    {"types": {"Space": "unknown"}}, {"types": {"Space": []}},
    {"constructors": {"": {"parameters": [], "result": "shared"}}},
    {"constructors": {"kb.Vector": {"parameters": "identity", "result": "shared"}}},
    {"constructors": {"kb.Vector": {"parameters": ["bad"], "result": "shared"}}},
    {"constructors": {"kb.Vector": {"parameters": [], "result": "bad"}}},
    {"constructors": {"kb.Vector": {"parameters": [], "result": "shared", "other": 1}}},
])
def test_invalid_associated_schema(associated):
    spec = {"id": "empty", "version": "1", "associated": associated}
    for validate in (validate_interface, signature_from_spec):
        with pytest.raises(InterfaceError):
            validate(spec)


def test_exactly_one_id_or_term():
    spec = interface()
    value = spec["typing"]["types"]["Vector"]
    value["id"] = "also.nominal"
    with pytest.raises(InterfaceError, match="exactly one"):
        validate_interface(spec)
    del value["id"]
    del value["term"]
    with pytest.raises(InterfaceError, match="exactly one"):
        validate_interface(spec)


def test_associated_value_variable():
    spec = interface()
    spec["typing"]["types"]["Vector"]["term"] = {"var": "DocumentId", "kind": "shared"}
    assert validate_interface(spec)["typing"]["types"]["Vector"]["term"]["var"] == "DocumentId"


def test_same_term_aliases_cannot_disagree_on_representation():
    spec = interface()
    alias = deepcopy(spec["typing"]["types"]["Vector"])
    alias["representation"] = "str"
    spec["typing"]["types"]["Alias"] = alias
    with pytest.raises(InterfaceError, match="inconsistent"):
        validate_interface(spec)


def test_legacy_nominal_and_nominal_term_have_same_identity():
    spec = interface()
    spec["typing"]["types"] = {
        "Vector": {"id": "kb.vector", "usage": "shared", "representation": "opaque"},
        "Alias": {"term": {"nominal": "kb.vector", "kind": "shared"}, "usage": "shared", "representation": "str"},
    }
    with pytest.raises(InterfaceError, match="inconsistent"):
        validate_interface(spec)


def test_nominal_string_cannot_impersonate_application_structure():
    spec = interface()
    spec["typing"]["types"]["Fake"] = {
        "id": 'kb.Vector[Space]', "usage": "shared", "representation": "str"
    }
    assert len(validate_interface(spec)["typing"]["types"]) == 2
