from __future__ import annotations

import copy
import hashlib
import json
import sys

import pytest

from module_families.planning import PlanningError, plan, select

SERVICE = {"id": "example.service", "version": "1"}
TYPES = {"id": "example.types", "version": "1"}


def card(name, *, provides=None, requires=None, kind="operation", effects=(), **extra):
    return {
        "family": "example",
        "id": name,
        "version": "0.1.0",
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
        "kind": kind,
        "provides": provides or SERVICE,
        "requires": requires or {},
        "effects": list(effects),
        **extra,
    }


def hole(name="service", requires=None):
    return {"hole": name, "requires": requires or SERVICE}


def test_closed_provider_vs_open_constructor_and_zero_argument_factory():
    candidates = {
        "closed": card("closed"),
        "zero": card(
            "zero",
            kind="module-factory",
            contract_target="module-returned-by-zero-argument-factory",
        ),
        "open": card(
            "open",
            kind="functor",
            requires={"service": SERVICE},
            effects=["dependency-effects"],
        ),
    }
    result = plan(hole(), candidates)
    assert result["status"] == "ambiguous"
    assert [item["bindings"] for item in result["solutions"]] == [
        {"service": "closed"},
        {"service": "zero"},
    ]
    assert any(
        item["code"] == "open-provider" and item["alias"] == "open"
        for item in result["rejections"]
    )
    assert plan({"use": "open"}, candidates)["status"] == "unsatisfied"
    applied = plan({"use": "open", "with": {"service": {"use": "zero"}}}, candidates)
    assert applied["status"] == "unique"
    assert plan({"use": "closed", "with": {}}, candidates)["status"] == "unsatisfied"


def test_contract_version_and_slots_are_hard_constraints_not_scores():
    candidates = {
        "good": card("good", score=-100),
        "wrong": card(
            "wrong", provides={"id": SERVICE["id"], "version": "2"}, score=1000
        ),
        "wrap": card("wrap", kind="functor", requires={"service": SERVICE}),
    }
    result = plan(hole(), candidates)
    assert result["status"] == "unique"
    assert select(result)["bindings"] == {"service": "good"}
    wrong = plan({"use": "wrap", "with": {"service": {"use": "wrong"}}}, candidates)
    assert wrong["status"] == "unsatisfied"
    assert wrong["rejections"][0]["code"] == "contract-mismatch"
    missing = plan({"use": "wrap", "with": {}}, candidates)
    assert missing["rejections"][0]["code"] == "slot-mismatch"
    extra = plan(
        {"use": "wrap", "with": {"service": {"use": "good"}, "other": {"use": "good"}}},
        candidates,
    )
    assert extra["rejections"][0]["extra"] == ["other"]


def test_repeated_holes_share_a_provider_and_conflicting_contracts_error():
    candidates = {
        "a": card("a"),
        "b": card("b"),
        "pair": card(
            "pair", kind="functor", requires={"left": SERVICE, "right": SERVICE}
        ),
    }
    expression = {"use": "pair", "with": {"left": hole("same"), "right": hole("same")}}
    result = plan(expression, candidates)
    assert result["candidate_assignments"] == 2
    for solution in result["solutions"]:
        assert (
            solution["expression"]["with"]["left"]
            == solution["expression"]["with"]["right"]
        )
    expression["with"]["right"] = hole("same", TYPES)
    with pytest.raises(PlanningError, match="different contract"):
        plan(expression, candidates)


def test_type_sharing_rejects_known_conflicts_and_preserves_runtime_residuals():
    candidates = {
        "left": card("left", type_exports={"Money": "money-v1"}),
        "right": card("right", type_exports={"Money": "money-v2"}),
        "pair": card(
            "pair",
            kind="functor",
            requires={"left": SERVICE, "right": SERVICE},
            sharing=[["left.Money", "right.Money"]],
        ),
    }
    expression = {
        "use": "pair",
        "with": {"left": {"use": "left"}, "right": {"use": "right"}},
    }
    rejected = plan(expression, candidates)
    assert rejected["status"] == "unsatisfied"
    assert rejected["rejections"][0]["code"] == "type-sharing-mismatch"
    del candidates["right"]["type_exports"]
    unresolved = plan(expression, candidates)
    assert unresolved["status"] == "unique"
    assert any(
        item["kind"] == "runtime-sharing"
        for item in select(unresolved)["residual_obligations"]
    )
    candidates["right"]["type_exports"] = {"Money": "money-v1"}
    checked = plan(expression, candidates)
    assert not any(
        item["kind"] == "runtime-sharing"
        for item in select(checked)["residual_obligations"]
    )


def test_effect_substitution_is_conservative_and_unknown_cannot_satisfy_policy():
    candidates = {
        "network": card("network", effects=["network"]),
        "pure": card("pure"),
        "unknown": card("unknown", effects=["unknown"]),
        "callback": card("callback", effects=["dependency-effects"]),
        "wrap": card(
            "wrap",
            kind="functor",
            requires={"service": SERVICE},
            effects=["dependency-effects", "local-state"],
        ),
    }
    expression = {"use": "wrap", "with": {"service": {"use": "network"}}}
    result = plan(expression, candidates, allowed_effects=["network", "local-state"])
    assert select(result)["effects"] == ["local-state", "network"]
    assert (
        plan(expression, candidates, allowed_effects=["local-state"])["status"]
        == "unsatisfied"
    )
    assert (
        plan({"use": "unknown"}, candidates, allowed_effects=["unknown"])["status"]
        == "unsatisfied"
    )
    assert (
        plan({"use": "callback"}, candidates, allowed_effects=[])["status"]
        == "unsatisfied"
    )
    assert select(plan({"use": "callback"}, candidates))["effects"] == [
        "dependency-effects"
    ]
    assert select(plan(hole(), candidates, allowed_effects=[]))["bindings"] == {
        "service": "pure"
    }


def test_behavior_claims_remain_residual_obligations():
    candidates = {
        "service": card(
            "service",
            behavior={"idempotent": True},
            assumes=["stable idempotency keys"],
            guarantees=["at most one completed effect"],
        )
    }
    result = select(plan({"use": "service"}, candidates))
    residuals = result["residual_obligations"]
    assert {entry["field"] for entry in residuals if entry["kind"] == "behavioral"} == {
        "behavior",
        "assumes",
        "guarantees",
    }
    assert any(entry["kind"] == "runtime-interface" for entry in residuals)


def test_budgets_report_incompleteness_and_never_choose_an_arbitrary_winner():
    candidates = {name: card(name) for name in ("a", "b", "c")}
    complete = plan(hole(), candidates)
    assert complete["status"] == "ambiguous"
    assert complete["complete_for_candidates"]
    with pytest.raises(PlanningError, match="explicit index"):
        select(complete)
    assert select(complete, 1)["bindings"] == {"service": "b"}
    for result in (
        plan(hole(), candidates, max_solutions=1),
        plan(hole(), candidates, max_states=1),
    ):
        assert result["status"] == "incomplete"
        assert not result["complete_for_candidates"]
        assert result["visited_states"] == 1
        with pytest.raises(PlanningError):
            select(result)
        assert select(result, 0)["bindings"] == {"service": "a"}
    exact = plan(hole(), {"a": card("a")}, max_solutions=1, max_states=1)
    assert exact["status"] == "unique"
    assert exact["complete_for_candidates"]


def test_no_candidates_is_unsatisfied_relative_to_supplied_set():
    result = plan(hole(), {})
    assert result["status"] == "unsatisfied"
    assert result["complete_for_candidates"]
    assert result["candidate_assignments"] == result["visited_states"] == 0
    with pytest.raises(PlanningError):
        select(result, 0)


def test_planning_is_import_free_deterministic_and_does_not_mutate_metadata(tmp_path):
    marker = tmp_path / "executed"
    candidates = {
        "a": card("a", import_module="unimportable_candidate", wheel=str(marker))
    }
    original = copy.deepcopy(candidates)
    first = plan(hole(), candidates)
    second = plan(hole(), candidates)
    assert first == second
    assert candidates == original
    assert "unimportable_candidate" not in sys.modules
    assert not marker.exists()
    json.dumps(first, allow_nan=False)


@pytest.mark.parametrize(
    "expression", [[], {}, {"hole": "x"}, {"use": "missing"}, {"use": "a", "run": True}]
)
def test_malformed_expressions_are_errors(expression):
    with pytest.raises(PlanningError):
        plan(expression, {"a": card("a")})
