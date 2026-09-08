from __future__ import annotations

import copy
import sys

import pytest
from test_assemblies import CALL, interface, provider, publish, reseal
from test_assemblies import repo as repo

from module_families.assemblies import (
    AssemblyError,
    instantiate,
    lock_assembly,
    verify_assembly,
)
from module_families.synthesis import SynthesisError, read_goal, synthesize


def goal(*capabilities, requires=CALL, **extra):
    return {
        "schema_version": 1,
        "goal": {
            "name": "automatic-program",
            "requires": requires,
            "capabilities": list(capabilities),
        },
        **extra,
    }


def retry_members():
    return [
        provider(
            package="retry",
            kind="functor",
            requires={"client": CALL},
            effects=["dependency-effects"],
            capabilities=["retry"],
            behavior={"retry": "one additional attempt after ValueError"},
        )
    ]


RETRY_SOURCE = "def run(client):\n    def invoke(value):\n        try:\n            return client.run(value)\n        except ValueError:\n            return client.run(value)\n    return {'run': invoke}\n"


def test_goal_synthesizes_retry_over_a_client_without_a_predeclared_expression(
    tmp_path, repo
):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(capabilities=["client"], effects=["local-state"])],
        "seen = set()\ndef run(value):\n    if value not in seen:\n        seen.add(value)\n        raise ValueError('first call')\n    return value\n",
    )
    publish(tmp_path, repo, "retry", retry_members(), RETRY_SOURCE)
    request = goal("retry", policy={"allowed_effects": ["local-state"]})
    result = synthesize(request, repo)
    assert result["status"] == "unique" and result["complete_for_bounds"]
    solution = result["solutions"][0]
    assert {card["family"] for card in solution["candidates"].values()} == {
        "alpha",
        "retry",
    }
    expression = solution["expression"]
    assert solution["candidates"][expression["use"]]["family"] == "retry"
    assert set(expression["with"]) == {"client"}
    assert solution["capabilities"] == ["client", "retry"]
    assert solution["effects"] == ["local-state"]
    assert any(
        item["kind"] == "capability" and item["verified"] is False
        for item in solution["residual_obligations"]
    )
    assert not {"alpha", "retry"} & set(sys.modules)
    lock = lock_assembly(result, repo)
    assert lock["synthesis"]["request"] == request
    assert lock["synthesis"]["bounds"] == result["bounds"]
    instance = instantiate(lock, repo, tmp_path / "installed")
    assert instance.run(5) == 5


def test_named_constructor_slots_are_discovered_and_four_programs_remain_ambiguous(
    tmp_path, repo
):
    batch = {"id": "test.batch", "version": "1"}
    unique = {"id": "test.unique", "version": "1"}
    publish(
        tmp_path,
        repo,
        "streaminterfaces",
        interfaces=[
            interface(batch, callables=["run"]),
            interface(unique, callables=["run"]),
        ],
    )
    for family, contract, capability in [
        ("batchone", batch, "batch"),
        ("batchtwo", batch, "batch"),
        ("uniqueone", unique, "deduplicate"),
        ("uniquetwo", unique, "deduplicate"),
    ]:
        publish(
            tmp_path,
            repo,
            family,
            [provider(package=family, provides=contract, capabilities=[capability])],
            "def run(value):\n    return value\n",
        )
    publish(
        tmp_path,
        repo,
        "pipeline",
        [
            provider(
                package="pipeline",
                kind="functor",
                requires={"chunker": batch, "unique": unique},
                effects=["dependency-effects"],
                capabilities=["pipeline"],
            )
        ],
        "def run(chunker, unique):\n    def invoke(value):\n        return chunker.run(unique.run(value))\n    return {'run': invoke}\n",
    )
    result = synthesize(
        goal("deduplicate", "batch", policy={"allowed_effects": []}), repo
    )
    assert result["status"] == "ambiguous" and result["complete"]
    assert len(result["solutions"]) == 4
    assert all(
        set(solution["expression"]["with"]) == {"chunker", "unique"}
        for solution in result["solutions"]
    )
    with pytest.raises(AssemblyError, match="explicit choice"):
        lock_assembly(result, repo)
    lock = lock_assembly(result, repo, choice=2)
    assert instantiate(lock, repo, tmp_path / "installed").run(3) == 3
    assert (
        synthesize(goal("deduplicate", "batch", policy={"allowed_effects": []}), repo)
        == result
    )


def test_hard_effect_policy_rejects_a_declared_capability_without_executing_it(
    tmp_path, repo
):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(capabilities=["retry"], effects=["network"])],
        "def run(value):\n    raise AssertionError('must not run')\n",
    )
    result = synthesize(goal("retry", policy={"allowed_effects": []}), repo)
    assert result["status"] == "unsatisfied" and result["complete"]
    assert not result["solutions"]
    assert "alpha" not in sys.modules


def test_declared_type_sharing_mismatch_rejects_a_synthesized_tree(tmp_path, repo):
    left = {"id": "test.left", "version": "1"}
    right = {"id": "test.right", "version": "1"}
    publish(
        tmp_path,
        repo,
        "typeinterfaces",
        interfaces=[
            interface(left, types=["Record"]),
            interface(right, types=["Record"]),
        ],
    )
    for family, contract in [("left", left), ("right", right)]:
        publish(
            tmp_path,
            repo,
            family,
            [
                provider(
                    package=family,
                    symbol=f"{family}:Record",
                    kind="type",
                    provides=contract,
                    type_exports={"Record": family},
                )
            ],
            "class Record: pass\n",
        )
    publish(
        tmp_path,
        repo,
        "join",
        [
            provider(
                package="join",
                kind="functor",
                requires={"left": left, "right": right},
                sharing=[["left.Record", "right.Record"]],
                effects=["dependency-effects"],
                capabilities=["joined"],
            )
        ],
        "def run(left, right):\n    return {'run': lambda value: value}\n",
    )
    result = synthesize(goal("joined"), repo)
    assert result["status"] == "unsatisfied"
    assert not result["solutions"]


def test_constructor_cycles_and_depth_limits_are_incomplete_not_unsatisfied(
    tmp_path, repo
):
    publish(tmp_path, repo, "retry", retry_members(), RETRY_SOURCE)
    cyclic = synthesize(goal("retry"), repo)
    assert cyclic["status"] == "incomplete"
    assert cyclic["structural_cutoffs"] == ["constructor-repetition"]
    assert not cyclic["solutions"]
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    shallow = synthesize(goal("retry"), repo, max_depth=1)
    assert shallow["status"] == "incomplete"
    assert "max_depth" in shallow["structural_cutoffs"]
    assert synthesize(goal("retry"), repo, max_depth=2)["status"] == "unique"


@pytest.mark.parametrize(
    "budget,reason",
    [
        ({"max_candidates": 1}, "max_candidates"),
        ({"max_states": 1}, "max_states"),
        ({"max_solutions": 1}, "max_solutions"),
    ],
)
def test_truncated_search_never_claims_unique(tmp_path, repo, budget, reason):
    for family in ("alpha", "beta"):
        publish(
            tmp_path,
            repo,
            family,
            [provider(package=family, capabilities=["client"])],
            "def run(value):\n    return value\n",
        )
    result = synthesize(goal("client"), repo, **budget)
    assert result["status"] == "incomplete" and not result["complete_for_bounds"]
    assert reason in result["truncation"]
    with pytest.raises(AssemblyError, match="explicit choice"):
        lock_assembly(result, repo)


def test_only_an_explicit_preference_minimizes_actual_artifact_count(tmp_path, repo):
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    publish(tmp_path, repo, "retry", retry_members(), RETRY_SOURCE)
    alternatives = synthesize(goal(), repo)
    assert alternatives["status"] == "ambiguous"
    selected = synthesize(goal(preferences={"selection": "min_artifacts"}), repo)
    assert selected["status"] == "unique"
    assert selected["solutions"][0]["artifact_count"] == 2
    assert len(selected["solutions"][0]["candidates"]) == 1
    assert lock_assembly(selected, repo)["synthesis"]["request"]["preferences"] == {
        "selection": "min_artifacts"
    }


@pytest.mark.parametrize("change", ["goal", "capabilities", "bounds", "signature"])
def test_synthesis_lock_commits_goal_bounds_and_interface_metadata(
    tmp_path, repo, change
):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(capabilities=["client"])],
        "def run(value):\n    return value\n",
    )
    lock = lock_assembly(synthesize(goal("client"), repo), repo)
    changed = copy.deepcopy(lock)
    if change == "goal":
        changed["synthesis"]["request"]["goal"]["capabilities"] = ["unprovided"]
    elif change == "capabilities":
        changed["synthesis"]["capabilities"] = ["unprovided"]
    elif change == "bounds":
        changed["synthesis"]["bounds"]["max_depth"] = 0
    else:
        changed["interfaces"][0]["callables"] = {}
    reseal(changed)
    with pytest.raises(ValueError):
        verify_assembly(changed, repo)


def test_goal_toml_and_invalid_fields_are_checked(tmp_path):
    path = tmp_path / "goal.toml"
    path.write_text(
        'schema_version = 1\n[goal]\nname = "retrying-client"\nrequires = {id = "test.call", version = "1"}\ncapabilities = ["retry"]\n[policy]\nallowed_effects = []\n'
    )
    assert read_goal(path)["goal"]["capabilities"] == ["retry"]
    with pytest.raises(SynthesisError):
        read_goal(goal(preferences={"selection": "pick-anything"}))
