from __future__ import annotations

from unittest.mock import Mock

import pytest

from module_families.registry import canonical_bytes
from module_families.worker_context import contribution_context

REF = {"id": "knowledge.search", "version": "1"}
DOC = {"id": "knowledge.documents", "version": "1"}
TASK = {
    "sha256": "a" * 64,
    "document": {
        "task": {
            "summary": "Preserve search citations",
            "requires": REF,
            "capabilities": ["citations"],
        },
        "policy": {"allowed_effects": []},
    },
}
ROOT = {
    "family": "kb",
    "id": "retrieval",
    "version": "1",
    "provides": REF,
    "requires": {"data": DOC},
    "effects": [],
}
STORE = {
    "family": "kb",
    "id": "store",
    "version": "1",
    "provides": DOC,
    "requires": {},
    "effects": [],
}


def repository():
    repo = Mock(spec=["search", "interface", "candidates"])
    repo.search.side_effect = lambda query, **kw: [ROOT]
    repo.interface.side_effect = lambda id, version: {
        "id": id,
        "version": version,
        "callables": {},
    }
    repo.candidates.side_effect = lambda id, version, **kw: (
        [ROOT] if id == REF["id"] else [STORE]
    )
    return repo


def test_context_includes_composable_dependencies_without_artifacts():
    repo = repository()
    result = contribution_context(TASK, repo)
    assert {item["id"] for item in result["candidates"]} == {"retrieval", "store"}
    assert {item["id"] for item in result["interfaces"]} == {REF["id"], DOC["id"]}
    assert result["task_sha256"] == TASK["sha256"]
    assert len(canonical_bytes(result)) < 1_000_000
    assert "incomplete discovery" in result["scope"]


def test_context_candidate_and_interface_bounds():
    result = contribution_context(
        TASK, repository(), max_candidates=1, max_interfaces=1
    )
    assert len(result["candidates"]) == 1
    assert len(result["interfaces"]) == 1
    assert result["truncations"]


def test_context_byte_bound_marks_truncation():
    repo = repository()
    large = {**ROOT, "description": "x" * 10_000}
    repo.search.side_effect = lambda query, **kw: [large]
    repo.candidates.side_effect = lambda *args, **kw: [large]
    result = contribution_context(TASK, repo, max_bytes=1000)
    assert len(canonical_bytes(result)) <= 1000
    assert "max_bytes" in result["truncations"]
    assert result["candidates"] == []


@pytest.mark.parametrize(
    "bounds",
    [
        {"max_candidates": 0},
        {"max_interfaces": 1001},
        {"max_depth": 17},
        {"max_bytes": 1},
    ],
)
def test_context_invalid_budget(bounds):
    with pytest.raises(ValueError):
        contribution_context(TASK, repository(), **bounds)
