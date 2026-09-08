from __future__ import annotations

import json
import sys

import pytest
from test_assemblies import (  # noqa: F401
    CALL,
    interface,
    provider,
    publish,
    request,
)
from test_assemblies import repo as repository_fixture
from test_environments import environment_source as environment_fixture

from module_families.assemblies import instantiate, lock_assembly, resolve_assembly
from module_families.module_build import (
    ModuleBuildError,
    build_module,
    read_module,
    resolve_module,
)
from module_families.synthesis import synthesize

repo = repository_fixture
environment_source = environment_fixture

SOURCE = """
def run(value):
    return value

def double(*, base):
    def run(value):
        return base.run(value) * 2
    return {"run": run}

def increment(*, base):
    def run(value):
        return base.run(value) + 1
    return {"run": run}

def answer(*, retrieval):
    def run(value):
        return retrieval.run(value)
    return {"run": run}

def counter():
    count = 0
    def run(value):
        nonlocal count
        count += 1
        return count
    return {"run": run}

def combine(*, left, right):
    def run(value):
        return [left.run(value), right.run(value)]
    return {"run": run}
"""


def setup(root, repository):
    members = [provider(package="components")]
    for name, slots in (
        ("double", ["base"]),
        ("increment", ["base"]),
        ("answer", ["retrieval"]),
        ("counter", []),
        ("combine", ["left", "right"]),
    ):
        members.append(
            provider(
                name,
                package="components",
                symbol=f"components:{name}",
                kind="functor" if slots else "module-factory",
                requires={s: CALL for s in slots},
            )
        )
    publish(root, repository, "components", members, SOURCE)


def selector(name):
    return {"select": {"family": "components", "member": "tests." + name}}


def graph(
    nodes=None, links=None, ports=None, export="wrapper.run", identifier="linked"
):
    return {
        "schema_version": 1,
        "family": {
            "name": "graphs",
            "version": "1.0.0",
            "description": "Open reusable graphs",
        },
        "publisher": {"name": "builder"},
        "module": {
            "id": identifier,
            "version": "1.0.0",
            "provides": CALL,
            "capabilities": ["linked"],
        },
        "ports": {"base": {"requires": CALL}} if ports is None else ports,
        "nodes": nodes or {"wrapper": selector("double")},
        "links": {"wrapper.base": "base"} if links is None else links,
        "exports": {"run": export},
    }


def close(root, repository, identifier="linked"):
    document = request(
        bindings={
            "compiled": {"family": "graphs", "member": "builder." + identifier},
            "base": {"family": "components", "member": "tests.run"},
        },
        expression={"use": "compiled", "with": {"base": {"use": "base"}}},
    )
    # The legacy test helper prefixes its own publisher; these are already qualified.
    document["bindings"]["compiled"]["member"] = "builder." + identifier
    lock = lock_assembly(resolve_assembly(document, repository), repository)
    return instantiate(lock, repository, root / "installed"), lock


def test_open_graph_is_published_constructor_and_synthesizable(tmp_path, repo):
    setup(tmp_path, repo)
    result = build_module(graph(), repo, tmp_path / "build")
    assert result["open_ports"] == ["base"]
    repo.publish(tmp_path / "build/index.json")
    instance, lock = close(tmp_path, repo)
    assert instance.run(4) == 8
    assert "components" not in sys.modules
    source = (tmp_path / "build/generated.py").read_text()
    assert "_nodes['wrapper'] =" in source
    assert "synthesize" not in source and "repository" not in source
    assert len(lock["bindings"]["compiled"]["artifacts"]) >= 3
    goal = {
        "schema_version": 1,
        "goal": {"name": "linked", "requires": CALL, "capabilities": ["linked"]},
    }
    synthesis = synthesize(goal, repo, max_depth=2)
    assert synthesis["solutions"]


def test_named_node_shares_instance_but_distinct_nodes_are_fresh(tmp_path, repo):
    setup(tmp_path, repo)
    for shared in (True, False):
        nodes = {"one": selector("counter"), "wrapper": selector("combine")}
        if not shared:
            nodes["two"] = selector("counter")
        doc = graph(
            nodes,
            {"wrapper.left": "one", "wrapper.right": "one" if shared else "two"},
            ports={},
            identifier="shared" if shared else "fresh",
        )
        out = tmp_path / doc["module"]["id"]
        build_module(doc, repo, out)
        repo.publish(out / "index.json")
        assembly = {
            "schema_version": 1,
            "assembly": {"name": "state"},
            "bindings": {
                "g": {"family": "graphs", "member": "builder." + doc["module"]["id"]}
            },
            "expression": {"use": "g"},
        }
        lock = lock_assembly(resolve_assembly(assembly, repo), repo)
        instance = instantiate(lock, repo, tmp_path / "installed")
        assert instance.run(0) == ([1, 2] if shared else [1, 1])
        another = instantiate(lock, repo, tmp_path / "installed")
        assert another.run(0) == ([1, 2] if shared else [1, 1])


def test_late_binding_through_explicit_ports_and_mixin_order(tmp_path, repo):
    setup(tmp_path, repo)
    for reverse, expected in ((False, 9), (True, 10)):
        doc = graph(
            {
                "first": selector("increment" if reverse else "double"),
                "second": selector("double" if reverse else "increment"),
                "answer": selector("answer"),
            },
            {
                "first.base": "base",
                "second.base": "first",
                "answer.retrieval": "second",
            },
            export="answer.run",
            identifier="reverse" if reverse else "forward",
        )
        out = tmp_path / doc["module"]["id"]
        build_module(doc, repo, out)
        repo.publish(out / "index.json")
        instance, _ = close(tmp_path, repo, doc["module"]["id"])
        assert instance.run(4) == expected


def test_published_partial_graph_can_be_linked_again(tmp_path, repo):
    setup(tmp_path, repo)
    build_module(graph(), repo, tmp_path / "open")
    repo.publish(tmp_path / "open/index.json")
    doc = graph(
        {
            "inner": {"select": {"family": "graphs", "member": "builder.linked"}},
            "wrapper": selector("increment"),
        },
        {"inner.base": "base", "wrapper.base": "inner"},
        identifier="nested",
    )
    build_module(doc, repo, tmp_path / "nested")
    repo.publish(tmp_path / "nested/index.json")
    instance, _ = close(tmp_path, repo, "nested")
    assert instance.run(4) == 9


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda d: d["links"].clear(), "unfilled"),
        (lambda d: d["links"].update({"wrapper.base": "wrapper"}), "cycle"),
        (lambda d: d["nodes"].update({"unused": selector("counter")}), "unused"),
        (
            lambda d: d.update(
                constraints={"same_instance": [["wrapper.base", "wrapper.nope"]]}
            ),
            "instance",
        ),
        (lambda d: d["exports"].update(run="wrapper.missing"), "export"),
    ],
)
def test_invalid_graph_fails_without_executing_code(tmp_path, repo, mutation, reason):
    setup(tmp_path, repo)
    doc = graph()
    mutation(doc)
    result = resolve_module(doc, repo)
    assert result["status"] == "unsatisfied"
    assert reason in str(result["rejections"])
    assert "components" not in sys.modules


def test_ambiguity_bounds_and_deterministic_wheels(tmp_path, repo):
    setup(tmp_path, repo)
    doc = graph()
    first = build_module(doc, repo, tmp_path / "one")
    second = build_module(doc, repo, tmp_path / "two")
    assert first["wheel_sha256"] == second["wheel_sha256"]

    def reverse_tables(value):
        if isinstance(value, dict):
            return {key: reverse_tables(value[key]) for key in reversed(value)}
        return value

    reordered = build_module(reverse_tables(doc), repo, tmp_path / "reordered")
    assert reordered["wheel_sha256"] == first["wheel_sha256"]
    doc["nodes"]["wrapper"] = {"select": {"requires": CALL}}
    result = resolve_module(doc, repo)
    assert result["status"] == "ambiguous"
    assert resolve_module(doc, repo, max_candidates=1)["status"] == "incomplete"
    with pytest.raises(ModuleBuildError, match="ambiguous"):
        build_module(doc, repo, tmp_path / "ambiguous")


def test_index_constraints_survive_open_publication_and_reject_wrong_provider(
    tmp_path, repo
):
    setup(tmp_path, repo)
    publish(
        tmp_path,
        repo,
        "indexed",
        [provider(package="indexed", index_exports={"Space": "model-A@1"})],
        SOURCE,
    )
    doc = graph(
        {
            "known": {"select": {"family": "indexed", "member": "tests.run"}},
            "wrapper": selector("combine"),
        },
        {"wrapper.left": "known", "wrapper.right": "base"},
    )
    doc["constraints"] = {"same_index": [["known.Space", "base.Space"]]}
    doc["indices"] = {"Space": "base.Space"}
    build_module(doc, repo, tmp_path / "index-build")
    repo.publish(tmp_path / "index-build/index.json")
    card = repo.versions("graphs", "builder.linked")[0]
    assert card["index_requires"] == {"base": {"Space": "model-A@1"}}
    with pytest.raises(ValueError, match="choice"):
        close(tmp_path, repo)
    doc2 = request(
        bindings={
            "compiled": {"family": "graphs", "member": "builder.linked"},
            "base": {"family": "indexed", "member": "tests.run"},
        },
        expression={"use": "compiled", "with": {"base": {"use": "base"}}},
    )
    doc2["bindings"]["compiled"]["member"] = "builder.linked"
    lock = lock_assembly(resolve_assembly(doc2, repo), repo)
    instance = instantiate(lock, repo, tmp_path / "installed")
    assert instance.run(4) == [4, 4]
    assert instance.module.metadata()["indices"] == {"Space": "model-A@1"}


def test_runtime_index_check_for_direct_generated_factory(tmp_path, repo):
    setup(tmp_path, repo)
    doc = graph()
    doc["indices"] = {"Space": "base.Space"}
    build_module(doc, repo, tmp_path / "build")
    repo.publish(tmp_path / "build/index.json")
    # A direct import still requires a sealed module carrying the declared index.
    from module_families.interfaces import signature_from_spec
    from module_families.runtime import atomic_import

    bundle = atomic_import(
        repo, {"g": repo.lock("graphs", "builder.linked")}, tmp_path / "installed"
    )
    base = signature_from_spec(interface(CALL, callables=["run"])).seal(
        {"run": lambda value: value}
    )
    with pytest.raises(ValueError, match="missing semantic index"):
        bundle["g"](base=base)


def test_toml_only_unknown_fields_and_invalid_paths(tmp_path):
    path = tmp_path / "module.json"
    path.write_text(json.dumps(graph()))
    with pytest.raises(ModuleBuildError, match="TOML"):
        read_module(path)
    doc = graph()
    doc["python"] = "arbitrary code"
    with pytest.raises(ModuleBuildError, match="invalid module document"):
        read_module(doc)


def test_conflicting_concrete_indices_fail_at_build_time(tmp_path, repo):
    setup(tmp_path, repo)
    for package, space in (("spacea", "A"), ("spaceb", "B")):
        publish(
            tmp_path,
            repo,
            package,
            [provider(package=package, index_exports={"Space": space})],
            SOURCE,
        )
    doc = graph(
        {
            "a": {"select": {"family": "spacea", "member": "tests.run"}},
            "b": {"select": {"family": "spaceb", "member": "tests.run"}},
            "wrapper": selector("combine"),
        },
        {"wrapper.left": "a", "wrapper.right": "b"},
        ports={},
    )
    doc["constraints"] = {"same_index": [["a.Space", "b.Space"]]}
    result = resolve_module(doc, repo)
    assert result["status"] == "unsatisfied"
    assert "semantic index mismatch" in str(result["rejections"])


def test_projection_renaming_and_contract_stub(tmp_path, repo):
    setup(tmp_path, repo)
    other = {"id": "test.renamed", "version": "1"}
    publish(
        tmp_path, repo, "renamed", interfaces=[interface(other, callables=["answer"])]
    )
    doc = graph()
    doc["module"]["provides"] = other
    doc["exports"] = {"answer": "wrapper.run"}
    build_module(doc, repo, tmp_path / "build")
    stub = (tmp_path / "build/contract.pyi").read_text()
    compile(stub, "contract.pyi", "exec")
    assert "def answer(" in stub and "base: _Port0" in stub
    work = json.loads((tmp_path / "build/work-contract.json").read_text())
    assert len(work["interfaces"]) == 2
    repo.publish(tmp_path / "build/index.json")
    instance, _ = close(tmp_path, repo)
    assert instance.answer(2) == 4
    assert "run" not in instance.module


def test_old_lock_replays_after_incremental_contribution(tmp_path, repo):
    setup(tmp_path, repo)
    build_module(graph(), repo, tmp_path / "build")
    repo.publish(tmp_path / "build/index.json")
    _, lock = close(tmp_path, repo)
    publish(
        tmp_path,
        repo,
        "newpublisher",
        [provider(package="newpublisher")],
        "def run(value):\n    return value + 99\n",
    )
    assert instantiate(lock, repo, tmp_path / "again").run(5) == 10


def test_cli_build_module_from_toml(tmp_path, repo, capsys):
    setup(tmp_path, repo)
    from module_families.cli import main

    path = tmp_path / "module.toml"
    path.write_text("""schema_version = 1
[family]
name = "graphs"
version = "1.0.0"
description = "Open reusable graphs"
[publisher]
name = "builder"
[module]
id = "linked"
version = "1.0.0"
provides = { id = "test.call", version = "1" }
[ports.base]
requires = { id = "test.call", version = "1" }
[nodes.wrapper.select]
family = "components"
member = "tests.double"
[links]
"wrapper.base" = "base"
[exports]
run = "wrapper.run"
""")
    assert main(["resolve-module", str(path), "--registry", str(repo.root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unique"
    assert (
        main(
            [
                "build-module",
                str(path),
                "--registry",
                str(repo.root),
                "--out",
                str(tmp_path / "build"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["open_ports"] == ["base"]


def test_compiled_graph_replays_in_offline_environment(
    tmp_path, repo, environment_source
):
    from module_families.environments import (
        lock_environment,
        run_environment,
        sync_environment,
    )

    setup(tmp_path, repo)
    build_module(graph(), repo, tmp_path / "build")
    repo.publish(tmp_path / "build/index.json")
    _, lock = close(tmp_path, repo)
    result = lock_environment(
        lock,
        repo,
        tmp_path / "environment",
        find_links=[str(environment_source.parent / "wheels")],
        no_index=True,
    )
    sync_environment(result["lock"], tmp_path / "venv")
    assert (
        run_environment(result["lock"], tmp_path / "venv", export="run", args=[7])[
            "result"
        ]
        == 14
    )


def test_nominal_type_constraint_checks_live_type_objects(tmp_path, repo):
    from test_assemblies import GROUP

    for package in ("typea", "typeb"):
        publish(
            tmp_path,
            repo,
            package,
            [
                {
                    "id": "group",
                    "version": "1.0.0",
                    "kind": "module",
                    "summary": "record group",
                    "exports": {
                        "Record": f"{package}:Record",
                        "make": f"{package}:make",
                    },
                    "provides": GROUP,
                    "effects": [],
                }
            ],
            "class Record:\n    pass\n\ndef make(value):\n    return Record()\n",
        )
    doc = graph(
        {
            "a": {"select": {"family": "typea", "member": "tests.group"}},
            "b": {"select": {"family": "typeb", "member": "tests.group"}},
        },
        {},
        ports={},
    )
    doc["module"]["provides"] = GROUP
    doc["exports"] = {"Record": "a.Record", "make": "b.make"}
    doc["constraints"] = {"same_type": [["a.Record", "b.Record"]]}
    build_module(doc, repo, tmp_path / "build")
    repo.publish(tmp_path / "build/index.json")
    assembly = {
        "schema_version": 1,
        "assembly": {"name": "types"},
        "bindings": {"g": {"family": "graphs", "member": "builder.linked"}},
        "expression": {"use": "g"},
    }
    lock = lock_assembly(resolve_assembly(assembly, repo), repo)
    with pytest.raises(ValueError, match="nominal type mismatch"):
        instantiate(lock, repo, tmp_path / "installed")


def test_raw_interface_preflight_runs_before_factories(tmp_path, repo):
    from module_families.module_runtime import prepare_graph

    setup(tmp_path, repo)
    doc = graph(
        {"base": selector("run"), "wrapper": selector("double")},
        {"wrapper.base": "base"},
        ports={},
    )
    selected = resolve_module(doc, repo)["solutions"][0]
    entered = []

    def factory(*, base):
        entered.append(True)
        return {"run": lambda value: value}

    with pytest.raises(ValueError, match="call"):
        prepare_graph(
            {"document": read_module(doc), **selected},
            {"base": lambda: None, "wrapper": factory},
            {},
        )
    assert entered == []


def test_internal_constructor_index_requirement_becomes_open_obligation(tmp_path, repo):
    setup(tmp_path, repo)
    publish(
        tmp_path,
        repo,
        "required",
        [
            provider(
                "double",
                package="required",
                symbol="required:double",
                kind="functor",
                requires={"base": CALL},
                index_requires={"base": {"Space": "required-space"}},
                index_exports={"Space": {"from": "base.Space"}},
            )
        ],
        SOURCE,
    )
    doc = graph(
        {"wrapper": {"select": {"family": "required", "member": "tests.double"}}}
    )
    result = resolve_module(doc, repo)
    assert result["solutions"][0]["index_requires"] == {
        "base": {"Space": "required-space"}
    }


def test_closed_graph_blob_tampering_fails_before_import(tmp_path, repo):
    setup(tmp_path, repo)
    doc = graph({"wrapper": selector("counter")}, {}, ports={})
    build_module(doc, repo, tmp_path / "build")
    repo.publish(tmp_path / "build/index.json")
    assembly = {
        "schema_version": 1,
        "assembly": {"name": "tamper"},
        "bindings": {"g": {"family": "graphs", "member": "builder.linked"}},
        "expression": {"use": "g"},
    }
    lock = lock_assembly(resolve_assembly(assembly, repo), repo)
    artifact = lock["bindings"]["g"]["member"]["sha256"]
    repo._blob(artifact).write_bytes(b"not the compiled graph")
    with pytest.raises(ValueError, match="hash|SHA|sha|digest"):
        instantiate(lock, repo, tmp_path / "installed")
