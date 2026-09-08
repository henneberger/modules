"""Cross-publisher composition without a combined repository index."""

import threading
from copy import deepcopy

import pytest
from test_assemblies import CALL, interface, provider, publish
from test_assemblies import repo as repo
from test_synthesis import RETRY_SOURCE, goal, retry_members

from module_families.assemblies import instantiate, lock_assembly
from module_families.federation import (
    FederatedRegistry,
    FederationError,
    open_federation,
)
from module_families.registry import Registry, RegistryError
from module_families.repository import RemoteRegistry, make_server
from module_families.synthesis import synthesize


def shards(tmp_path):
    left, right = Registry(tmp_path / "left"), Registry(tmp_path / "right")
    publish(
        tmp_path, left, "contracts", interfaces=[interface(CALL, callables=["run"])]
    )
    return left, right


def test_synthesis_and_selected_artifacts_span_independent_repositories(tmp_path, repo):
    left, right = shards(tmp_path)
    publish(
        tmp_path,
        left,
        "alpha",
        [provider(capabilities=["client"])],
        "def run(value):\n    return value + 1\n",
    )
    publish(tmp_path, right, "retry", retry_members(), RETRY_SOURCE)
    federation = FederatedRegistry(
        {"processing": right, "contracts-and-client": left}, page_size=1
    )
    result = synthesize(goal("retry"), federation)
    assert result["status"] == "unique"
    lock = lock_assembly(result, federation)
    instance = instantiate(lock, federation, tmp_path / "installed")
    assert instance.run(5) == 6
    assert len(federation._objects) >= 2
    with pytest.raises(FederationError, match="selected verified lock"):
        federation._blob("f" * 64)


def test_page_merge_deduplicates_identical_mirrors(tmp_path):
    left, right = shards(tmp_path)
    right.publish(tmp_path / "contracts/dist/index.json")
    for name, repository in [("alpha", left), ("beta", right), ("gamma", left)]:
        publish(
            tmp_path,
            repository,
            name,
            [provider(package=name)],
            "def run(value):\n    return value\n",
        )
    right.publish(tmp_path / "alpha/dist/index.json")
    federation = FederatedRegistry({"z": left, "a": right}, page_size=1)
    all_cards = federation.candidates(
        **{"contract_id": CALL["id"], "contract_version": CALL["version"]}
    )
    paged = [
        federation.candidates(CALL["id"], CALL["version"], limit=1, offset=i)[0]
        for i in range(3)
    ]
    assert all_cards == paged
    assert [c["family"] for c in paged] == ["alpha", "beta", "gamma"]
    assert len(federation.interfaces()) == 1
    assert len(federation.families()) == 4
    assert len(federation.search("", limit=10)) == 3


def test_interface_collision_fails_before_candidate_selection(tmp_path):
    left, right = shards(tmp_path)
    publish(
        tmp_path, right, "different", interfaces=[interface(CALL, callables=["other"])]
    )
    federation = FederatedRegistry({"a": left, "b": right})
    with pytest.raises(FederationError, match="collision"):
        federation.candidates(CALL["id"], CALL["version"])
    with pytest.raises(FederationError, match="collision"):
        federation.interfaces()


def test_same_member_different_immutable_content_rejected(tmp_path):
    left, right = shards(tmp_path)
    publish(
        tmp_path, left, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    other_root = tmp_path / "other"
    other_root.mkdir()
    publish(
        other_root,
        right,
        "alpha",
        [provider(summary="different")],
        "def run(value):\n    return value + 1\n",
    )
    federation = FederatedRegistry({"a": left, "b": right})
    with pytest.raises(FederationError, match="collision"):
        federation.inspect("alpha", "tests.run", "1.0.0")
    with pytest.raises(FederationError, match="collision"):
        federation.candidates(CALL["id"], CALL["version"])


def test_failure_is_not_treated_as_missing(tmp_path, monkeypatch):
    left, right = shards(tmp_path)

    def failed(*args):
        raise RegistryError("Repository request failed: timeout")

    monkeypatch.setattr(right, "interface", failed)
    with pytest.raises(FederationError, match="timeout"):
        FederatedRegistry({"a": left, "b": right}).interface(**CALL)


def test_scan_budget_reports_incomplete_instead_of_empty(tmp_path):
    left, right = shards(tmp_path)
    for name in ["alpha", "beta"]:
        publish(
            tmp_path,
            left,
            name,
            [provider(package=name)],
            "def run(value):\n    return value\n",
        )
    federation = FederatedRegistry({"a": left, "b": right}, page_size=1, max_scan=1)
    with pytest.raises(FederationError, match="scan bound"):
        federation.candidates(CALL["id"], CALL["version"], offset=1)


def test_http_shard_only_downloads_selected_artifacts(tmp_path, repo):
    left, right = shards(tmp_path)
    publish(
        tmp_path, right, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    publish(
        tmp_path,
        right,
        "unused",
        [provider(package="unused")],
        "def run(value):\n    return value\n",
    )
    server = make_server(right)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote = RemoteRegistry(
            f"http://127.0.0.1:{server.server_port}", cache=tmp_path / "cache"
        )
        federation = FederatedRegistry({"local": left, "remote": remote})
        lock = federation.lock("alpha", "tests.run", "1.0.0")
        assert not list((tmp_path / "cache").rglob("*.whl"))
        federation.materialize(lock, tmp_path / "materialized")
        assert len(list((tmp_path / "cache").rglob("*.whl"))) == len(lock["artifacts"])
        damaged = deepcopy(lock)
        damaged["sha256"] = "0" * 64
        with pytest.raises(FederationError, match="hash mismatch"):
            federation.materialize(damaged, tmp_path / "bad")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_toml_relative_shards_and_validation(tmp_path):
    left, _ = shards(tmp_path)
    path = tmp_path / "project.federation.toml"
    path.write_text(
        'schema_version=1\npage_size=1\n[shards.contracts]\nlocation="left"\n[shards.extensions]\nlocation="right"\n'
    )
    federation = open_federation(path)
    assert federation.interface(**CALL) == left.interface(**CALL)
    path.write_text('schema_version=1\n[shards.bad]\nlocation="left"\ntoken="secret"\n')
    with pytest.raises(FederationError, match="location"):
        open_federation(path)


def test_nested_federation_configuration_rejected_before_recursion(tmp_path):
    path = tmp_path / "cycle.federation.toml"
    path.write_text(
        'schema_version=1\n[shards.self]\nlocation="cycle.federation.toml"\n'
    )
    with pytest.raises(FederationError, match="nested federations"):
        open_federation(path)


def test_open_repository_dispatches_federation_manifest(tmp_path):
    from module_families.repository import open_repository

    left, _ = shards(tmp_path)
    path = tmp_path / "project.federation.toml"
    path.write_text('schema_version=1\n[shards.local]\nlocation="left"\n')
    opened = open_repository(path)
    assert isinstance(opened, FederatedRegistry)
    assert opened.interface(**CALL) == left.interface(**CALL)


def test_artifact_hash_checked_after_selection(tmp_path, monkeypatch):
    left, right = shards(tmp_path)
    publish(
        tmp_path, left, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    federation = FederatedRegistry({"a": left, "b": right})
    lock = federation.lock("alpha", "tests.run")
    bad = tmp_path / "bad.whl"
    bad.write_bytes(b"corrupt")
    monkeypatch.setattr(left, "_blob", lambda digest: bad)
    with pytest.raises(FederationError, match="artifact hash mismatch"):
        federation._blob(lock["artifacts"][0]["sha256"])


def test_exact_lock_closure_collision_rejected(tmp_path, monkeypatch):
    left, right = shards(tmp_path)
    publish(
        tmp_path, left, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    right.publish(tmp_path / "alpha/dist/index.json")
    original = right.lock

    def conflicting(*args):
        lock = original(*args)
        lock["external_requirements"] = ["different==1"]
        return lock

    monkeypatch.setattr(right, "lock", conflicting)
    with pytest.raises(FederationError, match="artifact closure collision"):
        FederatedRegistry({"a": left, "b": right}).lock("alpha", "tests.run")
