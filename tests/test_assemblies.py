from __future__ import annotations

import copy
import hashlib
import sys

import pytest

from module_families.assemblies import (
    AssemblyError,
    instantiate,
    lock_assembly,
    read_assembly,
    resolve_assembly,
    verify_assembly,
)
from module_families.compiler import build_family
from module_families.manifest import write_manifest
from module_families.registry import Registry, canonical_bytes

CALL = {"id": "test.call", "version": "1"}
TYPES = {"id": "test.types", "version": "1"}
GROUP = {"id": "test.group", "version": "1"}


def interface(reference, *, types=(), callables=()):
    return {
        **reference,
        "types": list(types),
        "callables": {
            name: {
                "parameters": [
                    {"name": "value", "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                ],
                "asynchronous": False,
            }
            for name in callables
        },
    }


def publish(root, repository, family, members=(), source=None, interfaces=()):
    project = root / family
    project.mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "name": family,
        "version": "1.0.0",
        "description": family,
        "publisher": "tests",
        "context": {},
        "members": list(members),
    }
    if source is not None:
        package = project / "src" / family
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(source)
        manifest["source"] = {"root": "src", "package": family}
    if interfaces:
        manifest["interfaces"] = list(interfaces)
    path = project / "family.toml"
    write_manifest(manifest, path)
    build_family(path, project / "dist")
    repository.publish(project / "dist/index.json")


def provider(identifier="run", version="1.0.0", package="alpha", **extra):
    return {
        "id": identifier,
        "version": version,
        "kind": "operation",
        "summary": identifier,
        "symbol": f"{package}:run",
        "provides": CALL,
        "effects": [],
        **extra,
    }


def request(
    selector=None, *, libraries=(), bindings=None, expression=None, policy=None
):
    selected_bindings = copy.deepcopy(
        bindings or {"selected": selector or {"requires": CALL}}
    )
    for item in selected_bindings.values():
        if "member" in item and not item["member"].startswith("tests."):
            item["member"] = "tests." + item["member"]
    return {
        "schema_version": 1,
        "assembly": {"name": "test-assembly", "type_libraries": list(libraries)},
        "bindings": selected_bindings,
        "expression": expression or {"use": "selected"},
        "policy": {} if policy is None else policy,
    }


def reseal(lock):
    lock["sha256"] = hashlib.sha256(
        canonical_bytes({key: value for key, value in lock.items() if key != "sha256"})
    ).hexdigest()
    return lock


@pytest.fixture
def repo(tmp_path):
    saved = {
        name: module
        for name, module in sys.modules.copy().items()
        if name in {"mf_cells", "mf_members"}
        or name.startswith(("mf_cells.", "mf_members."))
    }
    original_path = sys.path[:]
    for name in saved:
        sys.modules.pop(name, None)
    repository = Registry(tmp_path / "repository")
    publish(
        tmp_path,
        repository,
        "interfaces",
        interfaces=[
            interface(CALL, callables=["run"]),
            interface(TYPES, types=["Record"]),
            interface(GROUP, types=["Record"], callables=["make"]),
        ],
    )
    yield repository
    for name in list(sys.modules):
        if name in {"mf_cells", "mf_members"} or name.startswith(
            ("mf_cells.", "mf_members.")
        ):
            sys.modules.pop(name, None)
    sys.modules.update(saved)
    sys.path[:] = original_path


def test_repository_resolution_locks_and_instantiates_without_original_package(
    tmp_path, repo
):
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    document = tmp_path / "assembly.toml"
    document.write_text(
        'schema_version = 1\n[assembly]\nname = "identity"\n[bindings.selected]\nrequires = {id = "test.call", version = "1"}\nversion = ">=1,<2"\n[expression]\nuse = "selected"\n[policy]\nallowed_effects = []\n'
    )
    result = resolve_assembly(document, repo)
    assert result["status"] == "unique"
    assert "alpha" not in sys.modules
    lock = lock_assembly(result, repo)
    assert lock["environment"] == {"locked": False, "requirements": []}
    assert verify_assembly(lock)["cards"]["selected"]["family"] == "alpha"
    instance = instantiate(lock, repo, tmp_path / "installed")
    assert instance.run(7) == 7
    assert instance.module.identity == "assembly:" + lock["sha256"]
    assert instance.identity == lock["sha256"]
    assert "alpha" not in sys.modules


def test_versions_filter_before_candidate_budget_and_latest_is_pep440(tmp_path, repo):
    source = "def run(value):\n    return value\n"
    for version in ["1.0.0", "2.10.0", "2.9.0"]:
        publish(tmp_path, repo, "alpha", [provider(version=version)], source)
    exact = resolve_assembly(
        request({"family": "alpha", "member": "run", "version": "==1.0"}),
        repo,
        max_candidates=1,
    )
    assert exact["status"] == "unique"
    latest = resolve_assembly(
        request({"family": "alpha", "member": "run", "selection": "latest"}),
        repo,
        max_candidates=1,
    )
    assert latest["status"] == "unique"
    assert latest["solutions"][0]["candidates"]["selected"]["version"] == "2.10.0"
    alternatives = resolve_assembly(
        request({"family": "alpha", "member": "run", "version": ">=2"}), repo
    )
    assert alternatives["status"] == "ambiguous"
    with pytest.raises(AssemblyError, match="explicit choice"):
        lock_assembly(alternatives, repo)
    assert (
        lock_assembly(alternatives, repo, choice=1)["bindings"]["selected"]["version"]
        == "2.9.0"
    )


def test_latest_does_not_choose_between_unrelated_providers(tmp_path, repo):
    for family in ("alpha", "beta"):
        publish(
            tmp_path,
            repo,
            family,
            [provider(package=family)],
            "def run(value):\n    return value\n",
        )
    result = resolve_assembly(request({"requires": CALL, "selection": "latest"}), repo)
    assert result["status"] == "ambiguous"
    assert {
        item["candidates"]["selected"]["family"] for item in result["solutions"]
    } == {"alpha", "beta"}
    for budget in ({"max_solutions": 1}, {"max_states": 1}, {"max_candidates": 1}):
        limited = resolve_assembly(request(), repo, **budget)
        assert limited["status"] == "incomplete" and not limited["complete"]
        with pytest.raises(AssemblyError, match="explicit choice"):
            lock_assembly(limited, repo)


def test_candidate_search_pages_without_losing_provider_boundaries(tmp_path, repo):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(identifier=f"run{index:03d}") for index in range(101)],
        "def run(value):\n    return value\n",
    )
    result = resolve_assembly(request(), repo, max_candidates=101, max_solutions=102)
    assert result["status"] == "ambiguous" and result["complete"]
    assert result["candidate_counts"] == {"selected": 101}
    assert len(result["solutions"]) == 101
    limited = resolve_assembly(request(), repo, max_candidates=100, max_solutions=102)
    assert limited["status"] == "incomplete"


def grouped_members(*, effects=(), identity="shared-record"):
    return [
        {
            "id": "types",
            "kind": "module",
            "summary": "Shared records",
            "exports": {"Record": "shared:Record"},
            "provides": TYPES,
            "effects": list(effects),
            "type_exports": {"Record": identity},
        },
        {
            "id": "operations",
            "kind": "module",
            "summary": "Record constructor",
            "exports": {"Record": "shared:Record", "make": "shared:make"},
            "provides": GROUP,
            "effects": [],
            "type_exports": {"Record": identity},
        },
    ]


def grouped_request():
    return request(
        bindings={
            "records": {"family": "shared", "member": "types"},
            "operations": {"family": "shared", "member": "operations"},
        },
        expression={"use": "operations"},
        libraries=["records"],
        policy={"allowed_effects": ["local-state"]},
    )


def test_grouped_modules_share_types_and_library_effects_enter_lock(tmp_path, repo):
    publish(
        tmp_path,
        repo,
        "shared",
        grouped_members(effects=["local-state"]),
        "class Record:\n    def __init__(self, value):\n        self.value = value\ndef make(value):\n    return Record(value)\n",
    )
    result = resolve_assembly(grouped_request(), repo)
    assert result["status"] == "unique"
    lock = lock_assembly(result, repo)
    assert lock["effects"] == ["local-state"]
    instance = instantiate(lock, repo, tmp_path / "installed")
    assert type(instance.make(4)) is instance.Record
    assert instance.Record is instance.bundle["records"]["Record"]
    blocked = grouped_request()
    blocked["policy"]["allowed_effects"] = []
    assert resolve_assembly(blocked, repo)["status"] == "unsatisfied"


def test_type_identity_declarations_reject_mismatch_and_runtime_checks_false_claims(
    tmp_path, repo
):
    members = grouped_members()
    members[1]["exports"]["Record"] = "shared:Other"
    members[1]["type_exports"]["Record"] = "other-record"
    source = (
        "class Record: pass\nclass Other: pass\ndef make(value):\n    return value\n"
    )
    publish(tmp_path, repo, "shared", members, source)
    rejected = resolve_assembly(grouped_request(), repo)
    assert rejected["status"] == "unsatisfied"
    assert rejected["rejections"][0]["reasons"][0]["code"] == "type-library-mismatch"
    members[1]["version"] = "2.0.0"
    members[1]["type_exports"]["Record"] = "shared-record"
    publish(tmp_path, repo, "shared", [members[1]], source)
    request_ = grouped_request()
    request_["bindings"]["operations"]["version"] = "==2.0"
    lock = lock_assembly(resolve_assembly(request_, repo), repo)
    with pytest.raises(ValueError, match="identity|type"):
        instantiate(lock, repo, tmp_path / "invalid")


def test_invalid_runtime_callable_never_returns_a_completed_assembly(tmp_path, repo):
    publish(tmp_path, repo, "alpha", [provider()], "def run():\n    return 0\n")
    lock = lock_assembly(resolve_assembly(request(), repo), repo)
    with pytest.raises(ValueError, match="call|argument|parameter"):
        instantiate(lock, repo, tmp_path / "invalid")


@pytest.mark.parametrize(
    "mutation", ["hash", "interface", "residual", "environment", "member", "policy"]
)
def test_lock_tampering_and_signature_substitution_are_rejected(
    tmp_path, repo, mutation
):
    publish(
        tmp_path,
        repo,
        "alpha",
        [provider(behavior={"claim": "identity"})],
        "def run(value):\n    return value\n",
    )
    lock = lock_assembly(resolve_assembly(request(), repo), repo)
    changed = copy.deepcopy(lock)
    if mutation == "hash":
        changed["name"] = "changed"
    elif mutation == "interface":
        changed["interfaces"][0]["callables"]["run"]["parameters"][0]["name"] = (
            "substituted"
        )
    elif mutation == "residual":
        changed["residual_obligations"] = []
    elif mutation == "environment":
        changed["environment"]["locked"] = True
    elif mutation == "member":
        changed["bindings"]["selected"]["member"]["behavior"] = {}
        reseal(changed["bindings"]["selected"])
    else:
        changed["policy"] = {"ignored": True}
    if mutation != "hash":
        reseal(changed)
    with pytest.raises(ValueError):
        verify_assembly(changed, repo)


def test_lock_identity_commits_expression_and_rejects_resolution_substitution(
    tmp_path, repo
):
    for family in ("alpha", "beta"):
        publish(
            tmp_path,
            repo,
            family,
            [provider(package=family)],
            "def run(value):\n    return value\n",
        )
    publish(
        tmp_path,
        repo,
        "choice",
        [
            provider(
                package="choice",
                kind="functor",
                requires={"left": CALL, "right": CALL},
                effects=["dependency-effects"],
            )
        ],
        "def run(left, right):\n    return left\n",
    )
    source = request(
        bindings={
            "choose": {"family": "choice", "member": "run"},
            "a": {"family": "alpha", "member": "run"},
            "b": {"family": "beta", "member": "run"},
        },
        expression={
            "use": "choose",
            "with": {"left": {"use": "a"}, "right": {"use": "b"}},
        },
    )
    first_result = resolve_assembly(source, repo)
    first = lock_assembly(first_result, repo)
    source["expression"]["with"] = {"left": {"use": "b"}, "right": {"use": "a"}}
    second = lock_assembly(resolve_assembly(source, repo), repo)
    assert first["bindings"] == second["bindings"]
    assert first["sha256"] != second["sha256"]
    first_result["solutions"][0]["expression"] = source["expression"]
    with pytest.raises(AssemblyError, match="differs"):
        lock_assembly(first_result, repo)


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["assembly"].update(type_libraries=[{}]),
        lambda r: r["bindings"]["selected"].update(version=3),
        lambda r: r.update(policy={"allowed_effects": [""]}),
    ],
)
def test_malformed_requests_raise_assembly_errors(edit):
    document = request()
    edit(document)
    with pytest.raises(AssemblyError):
        read_assembly(document)
