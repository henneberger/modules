"""The optional acceptance workflow must not publish untested additions."""

from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from module_families import acceptance as a
from module_families.contributions import ContributionError


@pytest.fixture
def publication(tmp_path, monkeypatch):
    interface = {"id": "kb.search", "version": "1", "types": [], "callables": {}}
    member = {
        "id": "alice.search",
        "publisher": "alice",
        "local_id": "search",
        "version": "1.0.0",
        "family": "knowledge",
        "sha256": "tested-wheel",
        "provides": {"id": "kb.search", "version": "1"},
        "capabilities": ["preserves-citations"],
    }
    artifacts = [
        {
            "distribution": "mf-alice-search",
            "sha256": "tested-wheel",
            "filename": "tested.whl",
        }
    ]
    index = {
        "family": {"name": "knowledge"},
        "publisher": "alice",
        "members": [member],
        "artifacts": artifacts,
        "interfaces": [interface],
    }
    lock = {
        "sha256": "environment",
        "assembly": {
            "sha256": "program",
            "expression": {"use": "root"},
            "interfaces": [interface],
            "bindings": {
                "root": {
                    "family": "knowledge",
                    "member": member,
                    "artifacts": artifacts,
                }
            },
        },
    }
    monkeypatch.setattr(
        a, "verify_environment", Mock(return_value=(copy.deepcopy(lock), None))
    )
    monkeypatch.setattr(
        a,
        "verify_evidence",
        Mock(return_value={"sha256": "evidence", "status": "passed"}),
    )
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    repository = Mock(spec=["publish"])
    repository.publish.return_value = {"members": 1}
    return index, path, repository


def accept(publication):
    index, path, repository = publication
    path.write_text(json.dumps(index))
    return a.accept_contribution(
        "task.json", "environment.json", "evidence.json", path, repository
    )


def test_exact_tested_root_is_accepted(publication):
    result = accept(publication)
    assert result["member"] == "alice.search"
    assert result["evidence_sha256"] == "evidence"
    assert result["environment_sha256"] == "environment"
    assert result["program_sha256"] == "program"
    publication[2].publish.assert_called_once_with(publication[1])
    a.verify_evidence.assert_called_once_with(
        "task.json", "environment.json", "evidence.json"
    )


def test_failed_evidence_prevents_publication(publication):
    a.verify_evidence.side_effect = ContributionError("evidence acceptance mismatch")
    with pytest.raises(ContributionError, match="acceptance"):
        accept(publication)
    publication[2].publish.assert_not_called()
    a.verify_environment.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-member",
        "missing-member",
        "changed-capabilities",
        "changed-wheel",
        "changed-publisher",
        "changed-family",
        "extra-artifact",
        "missing-artifact",
        "changed-artifact-hash",
        "extra-interface",
        "changed-interface",
    ],
)
def test_untested_publication_content_is_rejected(publication, mutation):
    index = publication[0]
    if mutation == "extra-member":
        index["members"].append({**index["members"][0], "id": "alice.untested"})
    elif mutation == "missing-member":
        index["members"] = []
    elif mutation == "changed-capabilities":
        index["members"][0]["capabilities"].append("another-claim")
    elif mutation == "changed-wheel":
        index["members"][0]["sha256"] = "untested-wheel"
    elif mutation == "changed-publisher":
        index["publisher"] = "mallory"
    elif mutation == "changed-family":
        index["family"]["name"] = "another-family"
    elif mutation == "extra-artifact":
        index["artifacts"].append(
            {
                "distribution": "untested",
                "sha256": "untested",
                "filename": "untested.whl",
            }
        )
    elif mutation == "missing-artifact":
        index["artifacts"] = []
    elif mutation == "changed-artifact-hash":
        index["artifacts"][0]["sha256"] = "untested"
    elif mutation == "extra-interface":
        index["interfaces"].append(
            {"id": "untested", "version": "1", "types": [], "callables": {}}
        )
    else:
        index["interfaces"][0]["types"] = ["NewType"]
    with pytest.raises(ContributionError):
        accept(publication)
    publication[2].publish.assert_not_called()


def test_omitting_already_published_interface_is_allowed(publication):
    publication[0].pop("interfaces")
    assert accept(publication)["member"] == "alice.search"


def test_registry_failure_is_not_reported_as_accepted(publication):
    publication[2].publish.side_effect = ValueError("artifact bytes changed")
    with pytest.raises(ValueError, match="artifact bytes"):
        accept(publication)


@pytest.mark.parametrize("field,value", [("family", []), ("artifacts", [None]), ("interfaces", {}), ("interfaces", [None])])
def test_malformed_index_rejected_without_publication(publication, field, value):
    publication[0][field] = value
    with pytest.raises(ContributionError):
        accept(publication)
    publication[2].publish.assert_not_called()
