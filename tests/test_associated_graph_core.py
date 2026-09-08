"""Scoped graph equations survive publication and nested specialization."""

from copy import deepcopy

import pytest

from module_families.associated_graph import graph_associated


def ref(name):
    return {"id": name, "version": "1"}


def term(tag, name, kind="identity"):
    return {tag: name, "kind": kind}


def interface(name, types):
    return {"id": name, "version": "1", "associated": {"types": types}}


def fixtures():
    interfaces = {("space", "1"): interface("space", {"Space": "identity"}),
                  ("result", "1"): interface("result", {"Output": "identity"})}
    doc = {"ports": {"left": {"requires": ref("space")}, "right": {"requires": ref("space")}},
           "module": {"provides": ref("result")}, "links": {}, "exports": {},
           "associated": {"types": {"Output": term("from", "left.Space")}},
           "constraints": {"same_associated": [["left.Space", "right.Space"]]}}
    return doc, interfaces


def test_open_graph_retains_nontrivial_constraint_for_next_composition():
    doc, interfaces = fixtures()
    result = graph_associated(doc, {}, [], interfaces)["associated"]
    assert result["types"] == {"Output": term("from", "left.Space")}
    assert result["sharing"] == [[term("from", "right.Space"), term("from", "left.Space")]]


def nested(witness_a, witness_b):
    doc, interfaces = fixtures()
    published = graph_associated(doc, {}, [], interfaces)["associated"]
    cards = {
        "first": {"provides": ref("space"), "associated": {"types": {"Space": term("nominal", witness_a)}}},
        "second": {"provides": ref("space"), "associated": {"types": {"Space": term("nominal", witness_b)}}},
        "nested": {"provides": ref("result"), "requires": {"left": {}, "right": {}}, "associated": published},
    }
    outer = {"ports": {}, "module": {"provides": ref("result")},
             "links": {"nested.left": "first", "nested.right": "second"}, "exports": {},
             "associated": {"types": {"Output": term("from", "nested.Output")}}, "constraints": {}}
    return outer, cards, interfaces


def test_closed_nested_graph_specializes_fully_without_residual_variables():
    doc, cards, interfaces = nested("model-a", "model-a")
    result = graph_associated(doc, cards, ["first", "second", "nested"], interfaces)["associated"]
    assert result == {"types": {"Output": term("nominal", "model-a")}, "constructors": {}, "sharing": []}


def test_closed_nested_graph_rejects_rigid_incompatible_witnesses():
    doc, cards, interfaces = nested("model-a", "model-b")
    with pytest.raises(ValueError, match="associated type mismatch.*nested sharing"):
        graph_associated(doc, cards, ["first", "second", "nested"], interfaces)


def test_repeated_nested_instantiations_do_not_capture_each_others_type_paths():
    doc, interfaces = fixtures()
    published = graph_associated(doc, {}, [], interfaces)["associated"]
    cards = {"one": {"provides": ref("result"), "requires": {"left": {}, "right": {}}, "associated": published},
             "two": {"provides": ref("result"), "requires": {"left": {}, "right": {}}, "associated": published}}
    interfaces[("pair", "1")] = interface("pair", {"First": "identity", "Second": "identity"})
    outer = {"ports": {"left": {"requires": ref("space")}, "right": {"requires": ref("space")}},
             "module": {"provides": ref("pair")}, "exports": {},
             "links": {"one.left": "left", "one.right": "left", "two.left": "right", "two.right": "right"},
             "constraints": {}, "associated": {"types": {
                 "First": term("from", "one.Output"), "Second": term("from", "two.Output")}}}
    result = graph_associated(outer, cards, ["one", "two"], interfaces)["associated"]
    assert result["types"] == {"First": term("from", "left.Space"), "Second": term("from", "right.Space")}
    assert result["sharing"] == []


def test_provider_requirement_specializes_public_port_and_keeps_obligation():
    doc, interfaces = fixtures()
    doc["constraints"] = {}
    doc["links"] = {"consumer.input": "left"}
    cards = {"consumer": {"provides": ref("space"), "requires": {"input": {}}, "associated": {
        "types": {"Space": term("from", "input.Space")},
        "requires": {"input": {"Space": term("nominal", "model-a")}}}}}
    result = graph_associated(doc, cards, ["consumer"], interfaces)["associated"]
    assert result["types"] == {"Output": term("nominal", "model-a")}
    assert result["sharing"] == [[term("from", "left.Space"), term("nominal", "model-a")]]


def test_free_variables_cannot_escape_via_provider_witnesses():
    doc, interfaces = fixtures()
    cards = {"bad": {"provides": ref("space"), "associated": {"types": {"Space": term("var", "hidden")}}}}
    with pytest.raises(ValueError, match="not free type variables"):
        graph_associated(doc, cards, ["bad"], interfaces)


def test_constructor_declarations_cannot_change_meaning_between_interfaces():
    doc, interfaces = fixtures()
    interfaces[("space", "1")]["associated"]["constructors"] = {"Vector": {"parameters": ["identity"], "result": "shared"}}
    interfaces[("result", "1")]["associated"]["constructors"] = {"Vector": {"parameters": ["identity"], "result": "linear"}}
    with pytest.raises(ValueError, match="conflicting type constructor"):
        graph_associated(doc, {}, [], interfaces)


def test_inputs_are_not_mutated():
    doc, interfaces = fixtures()
    before = deepcopy((doc, interfaces))
    graph_associated(doc, {}, [], interfaces)
    assert (doc, interfaces) == before
