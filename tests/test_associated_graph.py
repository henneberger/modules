"""Associated witnesses compose across publication boundaries without global aliases."""
from copy import deepcopy

import pytest
from test_assemblies import publish

from module_families.assemblies import instantiate, lock_assembly, resolve_assembly
from module_families.module_build import build_module, resolve_module
from module_families.registry import Registry

REGISTRY = {"kb.Vector": {"parameters": ["identity"], "result": "shared"}}


def nominal(name, kind="identity"):
    return {"nominal": name, "kind": kind}


def projection(path, kind="identity"):
    return {"from": path, "kind": kind}


def signature(identifier, *, name="Space", kind="identity", typed=False, usage="shared", mode="share"):
    spec = {
        "id": identifier, "version": "1", "callables": {"run": {"parameters": []}},
        "associated": {"types": {name: kind}, "constructors": REGISTRY},
    }
    if typed:
        term = {"apply": "kb.Vector", "args": [{"var": name, "kind": kind}], "kind": "shared"}
        if usage != "shared":
            term = {"var": name, "kind": kind}
            spec["callables"]["run"]["parameters"] = [{"name": "resource", "kind": "POSITIONAL_OR_KEYWORD", "required": True}]
        spec["typing"] = {
            "types": {"Value": {"term": term, "usage": usage, "representation": "opaque"}},
            "operations": {"run": {
                "parameters": {} if usage == "shared" else {"resource": {"type": "Value", "mode": mode}},
                "returns": ["Value"], "effects": [],
            }},
        }
    return spec


def reference(spec):
    return {"id": spec["id"], "version": spec["version"]}


def selection(family, member="run"):
    return {"select": {"family": family, "member": "tests." + member}}


def publish_provider(root, repo, family, spec, witnesses):
    publish(root, repo, family, members=[{
        "id": "run", "version": "1.0.0", "kind": "algorithm", "summary": "Fixture provider",
        "symbol": family + ":run", "provides": reference(spec), "effects": [],
        "associated": {"types": witnesses, "constructors": REGISTRY},
    }], source="def run(*args):\n    return args[0] if args else []\n")


def graph(result, *, nodes=None, ports=None, types=None, constraints=None):
    return {
        "schema_version": 1,
        "family": {"name": "graphs", "version": "1.0.0", "description": "Associated graph tests"},
        "publisher": {"name": "tests"},
        "module": {"id": "graph", "version": "1.0.0", "provides": reference(result)},
        "nodes": nodes or {}, "ports": ports or {}, "links": {},
        "exports": {"run": "embedding.run"},
        "associated": {"types": types or {"Space": projection("embedding.Space")}, "constructors": REGISTRY},
        "constraints": constraints or {},
    }


def setup(root, source=None, result=None):
    repo = Registry(root / "repository")
    source = source or signature("kb.provider")
    result = result or signature("kb.result")
    publish(root, repo, "contracts", interfaces=[source, result])
    return repo, source, result


def test_distinct_embedding_spaces_are_rejected(tmp_path):
    repo, source, result = setup(tmp_path)
    publish_provider(tmp_path, repo, "embedding", source, {"Space": nominal("embedding.v1")})
    publish_provider(tmp_path, repo, "index", source, {"Space": nominal("embedding.v2")})
    document = graph(result, nodes={"embedding": selection("embedding"), "index": selection("index")},
                     constraints={"same_associated": [["embedding.Space", "index.Space"]]})
    report = resolve_module(document, repo)
    assert report["status"] == "unsatisfied"
    assert "associated type mismatch" in str(report)


def test_result_witness_kind_must_match_interface(tmp_path):
    repo, source, result = setup(tmp_path)
    publish_provider(tmp_path, repo, "embedding", source, {"Space": nominal("embedding.v1")})
    document = graph(result, nodes={"embedding": selection("embedding")}, types={"Space": nominal("wrong.kind", "shared")})
    report = resolve_module(document, repo)
    assert report["status"] == "unsatisfied"
    assert "kind mismatch" in str(report)


@pytest.mark.parametrize("same_space", [True, False])
def test_open_sharing_survives_publication_and_nested_closure(tmp_path, same_space):
    repo, source, result = setup(tmp_path)
    publish(tmp_path, repo, "wrapper", members=[{
        "id": "run", "version": "1.0.0", "kind": "functor", "summary": "Projection fixture",
        "symbol": "wrapper:create", "provides": reference(source), "requires": {"base": reference(source), "index": reference(source)},
        "associated": {"types": {"Space": projection("base.Space")}, "constructors": REGISTRY},
    }], source="def create(*, base, index):\n    return {\"run\": base.run}\n")
    opened = graph(result, nodes={"wrapper": selection("wrapper")}, ports={name: {"requires": reference(source)} for name in ("embedding", "index")},
                   constraints={"same_associated": [["embedding.Space", "index.Space"]]})
    opened["links"] = {"wrapper.base": "embedding", "wrapper.index": "index"}
    opened["exports"] = {"run": "wrapper.run"}
    build_module(opened, repo, tmp_path / "built")
    repo.publish(tmp_path / "built/index.json")
    publish_provider(tmp_path, repo, "embedding", source, {"Space": nominal("embedding.v1")})
    publish_provider(tmp_path, repo, "index", source, {"Space": nominal("embedding.v1" if same_space else "embedding.v2")})
    closed = graph(result, nodes={"embedding": selection("graphs", "graph"), "actual": selection("embedding"), "index": selection("index")})
    closed["links"] = {"embedding.embedding": "actual", "embedding.index": "index"}
    report = resolve_module(closed, repo)
    assert report["status"] == ("unique" if same_space else "unsatisfied")
    if not same_space:
        assert "associated type mismatch" in str(report)
    else:
        closed["module"]["id"] = "closed"
        build_module(closed, repo, tmp_path / "closed")
        repo.publish(tmp_path / "closed/index.json")
        assembly = {
            "schema_version": 1, "assembly": {"name": "nested-associated"},
            "bindings": {"root": {"family": "graphs", "member": "tests.closed"}},
            "expression": {"use": "root"},
        }
        lock = lock_assembly(resolve_assembly(assembly, repo), repo)
        instance = instantiate(lock, repo, tmp_path / "installed")
        assert instance.run() == []



def test_nominal_document_ids_do_not_unify_due_to_equal_representation(tmp_path):
    source = signature("kb.documents", name="DocumentId", kind="shared")
    source["typing"] = {
        "types": {"Id": {"term": {"var": "DocumentId", "kind": "shared"}, "usage": "shared", "representation": "str"}},
        "operations": {"run": {"parameters": {}, "returns": ["Id"], "effects": []}},
    }
    result = deepcopy(source)
    result["id"] = "kb.result"
    repo, _, _ = setup(tmp_path, source, result)
    publish_provider(tmp_path, repo, "embedding", source, {"DocumentId": nominal("source.document", "shared")})
    publish_provider(tmp_path, repo, "index", source, {"DocumentId": nominal("other.document", "shared")})
    document = graph(result, nodes={"embedding": selection("embedding"), "index": selection("index")},
                     types={"DocumentId": projection("embedding.DocumentId", "shared")},
                     constraints={"same_associated": [["embedding.DocumentId", "index.DocumentId"]]})
    assert resolve_module(document, repo)["status"] == "unsatisfied"


@pytest.mark.parametrize("correct", [True, False])
def test_typed_vector_projection_substitutes_associated_space(tmp_path, correct):
    repo, source, result = setup(tmp_path, signature("kb.embedding", typed=True), signature("kb.result", typed=True))
    publish_provider(tmp_path, repo, "embedding", source, {"Space": nominal("embedding.v1")})
    document = graph(result, nodes={"embedding": selection("embedding")},
                     types={"Space": nominal("embedding.v1" if correct else "embedding.v2")})
    report = resolve_module(document, repo)
    assert report["status"] == ("unique" if correct else "unsatisfied")
    if not correct:
        assert "typed export contract mismatch" in str(report)


@pytest.mark.parametrize("mode", ["move", "borrow"])
def test_substitution_preserves_linear_parameter_mode_and_return(tmp_path, mode):
    source = signature("kb.resource", name="Session", kind="linear", typed=True, usage="linear", mode="move")
    result = deepcopy(source)
    result["id"] = "kb.result"
    result["typing"]["operations"]["run"]["parameters"]["resource"]["mode"] = mode
    repo, _, _ = setup(tmp_path, source, result)
    publish_provider(tmp_path, repo, "embedding", source, {"Session": nominal("session.v1", "linear")})
    document = graph(result, nodes={"embedding": selection("embedding")}, types={"Session": projection("embedding.Session", "linear")})
    report = resolve_module(document, repo)
    assert report["status"] == ("unique" if mode == "move" else "unsatisfied")
