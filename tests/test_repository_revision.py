"""Concurrent incremental publication cannot produce a false complete search."""
from __future__ import annotations

import threading

import pytest
from test_assemblies import provider, publish
from test_assemblies import repo as repo
from test_synthesis import goal

from module_families.assemblies import AssemblyError, lock_assembly
from module_families.federation import FederatedRegistry
from module_families.handoffs import plan_contributions
from module_families.registry import Registry, RegistryError
from module_families.repository import RemoteRegistry, make_server
from module_families.synthesis import synthesize
from module_families.workers import OverlayRepository


def test_publication_revision_is_idempotent(tmp_path, repo):
    before = repo.revision()
    repo.publish(tmp_path / "interfaces/dist/index.json")
    assert repo.revision() == before
    publish(tmp_path, repo, "alpha", [provider()], "def run(value): return value")
    assert repo.revision()["sequence"] == before["sequence"] + 1
    assert repo.revision()["publication_sha256"] != before["publication_sha256"]


@pytest.mark.parametrize("view", ["local", "federated", "overlay"])
def test_publication_during_search_requires_retry(tmp_path, repo, monkeypatch, view):
    publish(tmp_path, repo, "alpha", [provider()], "def run(value): return value")
    catalog = (repo if view == "local" else FederatedRegistry({"a": repo})
               if view == "federated" else OverlayRepository(repo))
    query = repo.candidates
    changed = False

    def publishing(*args, **kwargs):
        nonlocal changed
        page = query(*args, **kwargs)
        if not changed:
            changed = True
            publish(tmp_path, repo, "beta", [provider(package="beta")], "def run(value): return value")
        return page

    monkeypatch.setattr(repo, "candidates", publishing)
    result = synthesize(goal(), catalog)
    assert result["status"] == "incomplete"
    assert not result["complete_for_bounds"]
    assert "repository_changed" in result["truncation"]
    assert not result["repository_revision"]["unchanged"]
    with pytest.raises(AssemblyError, match="incomplete"):
        lock_assembly(result, catalog)
    stable = synthesize(goal(), catalog)
    assert stable["status"] == "ambiguous"
    assert stable["repository_revision"]["unchanged"]


def test_changed_catalog_does_not_generate_absence_handoff(tmp_path, repo, monkeypatch):
    query = repo.candidates
    changed = False

    def publishing(*args, **kwargs):
        nonlocal changed
        page = query(*args, **kwargs)
        if not changed:
            changed = True
            # An unrelated publication still invalidates the catalog-wide view.
            publish(tmp_path, repo, "other", interfaces=[{"id":"other.interface","version":"1","types":[],"callables":{}}])
        return page

    monkeypatch.setattr(repo, "candidates", publishing)
    result = plan_contributions(goal(), repo, tmp_path / "handoff")
    assert result["status"] == "incomplete"
    assert not result["tasks"]


def test_remote_revision_tracks_publications(tmp_path, repo):
    server = make_server(repo, tokens={"token": {"publisher": "tests"}}, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote = RemoteRegistry(f"http://127.0.0.1:{server.server_address[1]}", cache=tmp_path / "cache")
        assert remote.revision() == repo.revision()
        publish(tmp_path, repo, "alpha", [provider()], "def run(value): return value")
        assert synthesize(goal(), remote)["status"] == "unique"
        assert remote.revision() == repo.revision()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("record", [{"sequence": True, "publication_sha256": None},
                                    {"sequence": 1, "publication_sha256": None},
                                    {"sequence": 0, "publication_sha256": "a" * 64}])
def test_remote_revision_rejects_invalid_claim(tmp_path, monkeypatch, record):
    remote = RemoteRegistry("http://127.0.0.1:1", cache=tmp_path)
    monkeypatch.setattr(remote, "_request", lambda *args: record)
    with pytest.raises(RegistryError):
        remote.revision()


def test_empty_repository_revision(tmp_path):
    assert Registry(tmp_path / "empty").revision() == {"sequence": 0, "publication_sha256": None}


def test_exact_root_bypasses_unrelated_candidate_page_budget(tmp_path, repo, monkeypatch):
    publish(tmp_path, repo, "alpha", [provider()], "def run(value): return value")
    publish(tmp_path, repo, "target", [provider(package="target")], "def run(value): return value")
    card = repo.inspect("target", "tests.run", "1.0.0")
    request = goal()
    request["goal"]["root"] = {key: card[key] for key in ("family", "id", "version", "sha256")}

    def no_scan(*args, **kwargs):
        raise AssertionError("an exact root must not enumerate unrelated providers")

    monkeypatch.setattr(repo, "candidates", no_scan)
    result = synthesize(request, repo, max_candidates=1)
    assert result["status"] == "unique"
    assert result["complete_for_bounds"]
    assert lock_assembly(result, repo)["expression"]
