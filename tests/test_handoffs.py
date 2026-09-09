from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from test_assemblies import CALL, interface, provider, publish
from test_assemblies import repo as repo
from test_synthesis import goal

from module_families.handoffs import HandoffError, plan_contributions
from module_families.registry import canonical_bytes


def test_absent_root_produces_hash_bound_authorable_draft(tmp_path, repo):
    result = plan_contributions(
        goal("preserve-provenance", policy={"allowed_effects": []}),
        repo,
        tmp_path / "plan",
    )
    assert result["status"] == "needs-contributions"
    (task,) = result["tasks"]
    record = json.loads(Path(task["metadata_path"]).read_text())
    digest = record.pop("sha256")
    assert (
        digest == task["sha256"] == hashlib.sha256(canonical_bytes(record)).hexdigest()
    )
    assert record["interface"] == repo.interface(**CALL)
    assert record["origin"]["goal"]["capabilities"] == ["preserve-provenance"]
    draft_bytes = Path(task["draft_path"]).read_bytes()
    assert record["draft_sha256"] == hashlib.sha256(draft_bytes).hexdigest()
    draft = tomllib.loads(draft_bytes.decode())
    assert draft["task"]["requires"] == CALL
    assert draft["task"]["capabilities"] == []
    assert draft["policy"] == {"allowed_effects": []}
    assert "cases" not in draft
    assert record["status"] == "needs-acceptance-cases"
    assert Path(result["resolution_path"]).is_file()


def test_missing_nested_interface_deduplicated_across_alternatives(tmp_path, repo):
    missing = {"id": "test.document", "version": "1"}
    publish(tmp_path, repo, "documents", interfaces=[interface(missing)])
    for name in ("first", "second"):
        publish(
            tmp_path,
            repo,
            name,
            [
                provider(
                    package=name,
                    kind="functor",
                    requires={"document": missing},
                    effects=["dependency-effects"],
                )
            ],
            "def run(document):\n    raise AssertionError('never imported')\n",
        )
    result = plan_contributions(goal(), repo, tmp_path / "plan")
    assert result["status"] == "needs-contributions"
    assert [task["requires"] for task in result["tasks"]] == [missing]


def test_unknown_interface_requires_contract_not_implementation(tmp_path, repo):
    result = plan_contributions(
        goal(requires={"id": "unknown", "version": "1"}),
        repo,
        tmp_path / "plan",
    )
    assert not result["tasks"]
    assert result["missing_obligations"][0]["status"] == "needs-interface-contract"
    assert any(
        row["code"] == "unavailable-interface-contract" for row in result["diagnostics"]
    )


def test_successful_alternative_suppresses_unnecessary_work(tmp_path, repo):
    absent = {"id": "test.absent", "version": "1"}
    publish(tmp_path, repo, "missinginterface", interfaces=[interface(absent)])
    publish(
        tmp_path,
        repo,
        "branch",
        [
            provider(
                package="branch",
                kind="functor",
                requires={"missing": absent},
                effects=["dependency-effects"],
            )
        ],
        "def run(missing):\n    return missing\n",
    )
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    result = plan_contributions(goal(), repo, tmp_path / "plan")
    assert result["status"] == "resolved"
    assert result["tasks"] == []


@pytest.mark.parametrize(
    "bounds", [{"max_depth": 1}, {"max_states": 1}, {"max_candidates": 1}]
)
def test_search_cutoffs_do_not_become_missing_provider_tasks(tmp_path, repo, bounds):
    for name in ("alpha", "beta"):
        publish(
            tmp_path,
            repo,
            name,
            [
                provider(
                    package=name,
                    kind="functor",
                    requires={"client": CALL},
                    effects=["dependency-effects"],
                )
            ],
            "def run(client):\n    return client\n",
        )
    result = plan_contributions(goal(), repo, tmp_path / "plan", **bounds)
    assert result["status"] == "incomplete"
    assert not result["tasks"]
    assert result["truncation"] or result["structural_cutoffs"]


def test_policy_mismatch_does_not_request_existing_provider(tmp_path, repo):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(effects=["network"])],
        "def run(value):\n    return value\n",
    )
    result = plan_contributions(
        goal(policy={"allowed_effects": []}), repo, tmp_path / "plan"
    )
    assert result["status"] == "unsatisfied"
    assert not result["tasks"]
    assert any(
        row["code"] == "goal-or-contract-mismatch" for row in result["diagnostics"]
    )


def test_invalid_published_candidate_is_not_absence(tmp_path, repo):
    class InvalidRepository:
        def revision(self):
            return {"test": "fixed-invalid-catalog"}

        def candidates(self, *args, **kwargs):
            return [{"bad": "card"}] if kwargs["offset"] == 0 else []

        def interface(self, *args):
            raise AssertionError("invalid candidates do not justify a task")

    result = plan_contributions(goal(), InvalidRepository(), tmp_path / "plan")
    assert not result["tasks"]
    assert any(
        row["code"] == "providers-exist-but-none-selected"
        for row in result["diagnostics"]
    )


def test_never_overwrites_authored_acceptance_cases(tmp_path, repo):
    result = plan_contributions(goal(), repo, tmp_path / "plan")
    draft = Path(result["tasks"][0]["draft_path"])
    draft.write_text("author's work")
    with pytest.raises(HandoffError, match="new or empty"):
        plan_contributions(goal(), repo, tmp_path / "plan")
    assert draft.read_text() == "author's work"
