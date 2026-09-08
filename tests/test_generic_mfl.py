"""Associated types inside checked bodies, independently of provider selection."""

import json
import tomllib
from pathlib import Path

import pytest
from test_assemblies import publish

from module_families.interfaces import signature_from_spec, validate_interface
from module_families.manifest import write_manifest
from module_families.ownership import Owned, invoke
from module_families.registry import Registry
from module_families.type_terms import term_id
from module_families.typed_program import (
    TypeCheckError,
    _python,
    build_program,
    check_program,
)

REGISTRY = {"Owned": {"parameters": ["identity"], "result": "linear"}}


def term(name):
    return {
        "apply": "Owned",
        "args": [{"var": name, "kind": "identity"}],
        "kind": "linear",
    }


def interface(name, operation, parameters, returns):
    return validate_interface(
        {
            "id": name,
            "version": "1",
            "types": [],
            "associated": {"types": {"Domain": "identity"}, "constructors": REGISTRY},
            "callables": {
                operation: {
                    "parameters": [
                        {"name": p, "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                        for p in parameters
                    ]
                }
            },
            "typing": {
                "types": {
                    "Item": {
                        "term": term("Domain"),
                        "usage": "linear",
                        "representation": "opaque",
                    }
                },
                "operations": {
                    operation: {
                        "parameters": {
                            p: {"type": "Item", "mode": "move"} for p in parameters
                        },
                        "returns": returns,
                        "effects": [],
                    }
                },
            },
        }
    )


def prepare(
    tmp_path,
    sharing=True,
    source="item = source.make()\nresult = sink.pass_on(item)\nreturn result",
):
    source_spec = interface("generic.source", "make", [], ["Item"])
    sink_spec = interface("generic.sink", "pass_on", ["item"], ["Item"])
    result_spec = interface("generic.result", "run", [], ["Item"])
    repository = Registry(tmp_path / "repository")
    publish(
        tmp_path,
        repository,
        "contracts",
        interfaces=[source_spec, sink_spec, result_spec],
    )
    path = tmp_path / "program.toml"
    doc = {
        "schema_version": 1,
        "family": {
            "name": "generic",
            "version": "1.0.0",
            "description": "Generic body",
        },
        "publisher": {"name": "tests"},
        "module": {
            "id": "workflow",
            "version": "1.0.0",
            "provides": {"id": "generic.result", "version": "1"},
        },
        "ports": {
            p: {"requires": {"id": f"generic.{p}", "version": "1"}}
            for p in ("source", "sink")
        },
        "program": {"source": "program.mfl", "export": "run"},
        "associated": {
            "types": {"Domain": {"from": "source.Domain", "kind": "identity"}},
            "constructors": REGISTRY,
        },
    }
    if sharing:
        doc["associated"]["sharing"] = [
            [
                {"from": "source.Domain", "kind": "identity"},
                {"from": "sink.Domain", "kind": "identity"},
            ]
        ]
    write_manifest(doc, path)
    (tmp_path / "program.mfl").write_text(source)
    return repository, path, source_spec, sink_spec, result_spec


def witness(name):
    return {
        "types": {"Domain": {"nominal": name, "kind": "identity"}},
        "constructors": REGISTRY,
    }


def ports(source, sink, domain="documents", other=None, called=None):
    def make():
        if called is not None:
            called.append(True)
        return object()

    return {
        "source": signature_from_spec(source).seal(
            {"make": make}, associated=witness(domain)
        ),
        "sink": signature_from_spec(sink).seal(
            {"pass_on": lambda item: item}, associated=witness(other or domain)
        ),
    }


def test_separate_check_uses_only_explicit_sharing(tmp_path, monkeypatch):
    repo, path, *_ = prepare(tmp_path, sharing=False)
    with pytest.raises(TypeCheckError, match="nominal type mismatch"):
        check_program(path, repo)
    doc = tomllib.loads(path.read_text())
    doc["associated"]["sharing"] = [
        [
            {"from": "source.Domain", "kind": "identity"},
            {"from": "sink.Domain", "kind": "identity"},
        ]
    ]
    write_manifest(doc, path)
    monkeypatch.setattr(
        repo, "candidates", lambda *a, **k: pytest.fail("provider search")
    )
    report = check_program(path, repo)
    assert report["associated_assumptions"]["substitution"]
    assert report["guarantees"]["normal_path_usage"]


def test_runtime_rejects_wrong_domains_before_operations(tmp_path):
    repo, path, source, sink, _ = prepare(tmp_path)
    namespace = {}
    exec(_python(check_program(path, repo)), namespace)
    calls = []
    with pytest.raises(ValueError, match="sharing mismatch"):
        namespace["create"](
            **ports(source, sink, other="other-documents", called=calls)
        )
    assert calls == []
    module = namespace["create"](**ports(source, sink))
    value = module["run"]()
    expected = {
        "apply": "Owned",
        "args": [{"nominal": "documents", "kind": "identity"}],
        "kind": "linear",
    }
    assert isinstance(value, Owned) and value.type_id == term_id(expected)


def test_nested_checked_operation_uses_same_concrete_ownership_identity(tmp_path):
    repo, path, source, sink, _ = prepare(tmp_path)
    report = check_program(path, repo)
    namespace = {}
    exec(_python(report), namespace)
    inner = namespace["create"](**ports(source, sink))
    nested_source = signature_from_spec(source).seal(
        {"make": inner["run"]}, associated=witness("documents")
    )
    outer = namespace["create"](source=nested_source, sink=ports(source, sink)["sink"])
    value = outer["run"]()
    consumed = invoke(
        lambda item: None,
        [value],
        [
            {
                "id": value.type_id,
                "usage": "linear",
                "representation": "opaque",
                "mode": "move",
            }
        ],
        [],
    )
    assert consumed == () and not value.valid


def test_certificate_and_publication_preserve_equations(tmp_path):
    repo, path, *_ = prepare(tmp_path)
    first = build_program(path, repo, tmp_path / "first")
    second = build_program(path, repo, tmp_path / "second")
    a = json.loads(Path(first["index"]).read_text())
    b = json.loads(Path(second["index"]).read_text())
    assert a["members"][0]["associated"]["sharing"]
    assert a["members"][0]["sha256"] == b["members"][0]["sha256"]
    assert json.loads((tmp_path / "first/work-contract.json").read_text())[
        "associated_assumptions"
    ]["ports"]


def test_same_interface_in_distinct_ports_does_not_unify_implicitly(tmp_path):
    repo, path, *_ = prepare(
        tmp_path, sharing=False, source="item = source.make()\nreturn item"
    )
    doc = tomllib.loads(path.read_text())
    doc["ports"]["sink"]["requires"] = doc["ports"]["source"]["requires"]
    doc["associated"]["types"]["Domain"]["from"] = "sink.Domain"
    write_manifest(doc, path)
    with pytest.raises(TypeCheckError, match="return type mismatch"):
        check_program(path, repo)


def test_unknown_path_cannot_be_an_assumption(tmp_path):
    repo, path, *_ = prepare(tmp_path)
    doc = tomllib.loads(path.read_text())
    doc["associated"]["sharing"][0][1]["from"] = "sink.Missing"
    write_manifest(doc, path)
    with pytest.raises(TypeCheckError, match="missing or incompatible"):
        check_program(path, repo)


def test_runtime_primitive_witness_guard_before_adapter_runs(tmp_path):
    from module_families.typed_associated import prepare as symbolic
    from module_families.typed_associated import specialize_runtime

    spec = interface("primitive.result", "run", [], ["Item"])
    spec["associated"] = {"types": {"Value": "shared"}}
    spec["typing"]["types"]["Item"] = {
        "term": {"var": "Value", "kind": "shared"},
        "usage": "shared",
        "representation": "opaque",
    }
    spec = validate_interface(spec)
    doc = {
        "ports": {"input": {"requires": {"id": spec["id"], "version": "1"}}},
        "module": {"provides": {"id": spec["id"], "version": "1"}},
        "associated": {"types": {"Value": {"from": "input.Value", "kind": "shared"}}},
    }
    _, _, assumptions = symbolic(doc, [spec])
    module = signature_from_spec(spec).seal(
        {"run": lambda: object()},
        associated={"types": {"Value": {"nominal": "python.bool", "kind": "shared"}}},
    )
    with pytest.raises(ValueError, match="canonical primitive"):
        specialize_runtime(assumptions, {"input": module})


def test_owned_flow_still_checks_move_after_substitution(tmp_path):
    repo, path, *_ = prepare(
        tmp_path,
        source="item = source.make()\nresult = sink.pass_on(item)\nagain = sink.pass_on(item)\nreturn result",
    )
    with pytest.raises(TypeCheckError, match="use after move"):
        check_program(path, repo)


def test_generic_borrow_does_not_consume_owner(tmp_path):
    from copy import deepcopy

    repo, path, source, sink, output = prepare(
        tmp_path,
        source="item = source.make()\nsink.inspect(item)\nresult = sink.pass_on(item)\nreturn result",
    )
    sink["callables"]["inspect"] = deepcopy(sink["callables"]["pass_on"])
    sink["typing"]["operations"]["inspect"] = {
        "parameters": {"item": {"type": "Item", "mode": "borrow"}},
        "returns": [],
        "effects": [],
    }
    specs = {s["id"]: s for s in [source, sink, output]}

    class InterfacesOnly:
        def interface(self, name, version):
            return specs[name]

    report = check_program(path, InterfacesOnly())
    assert report["ir"][1]["parameters"][0]["mode"] == "borrow"
    namespace = {}
    exec(_python(report), namespace)
    provided = {
        "source": signature_from_spec(source).seal(
            {"make": lambda: object()}, associated=witness("documents")
        )
    }
    seen = []
    provided["sink"] = signature_from_spec(sink).seal(
        {"pass_on": lambda item: item, "inspect": lambda item: seen.append(item)},
        associated=witness("documents"),
    )
    result = namespace["create"](**provided)["run"]()
    assert result.valid and len(seen) == 1


def test_explicit_required_witness_allows_nominal_specialization(tmp_path):
    repo, path, *_ = prepare(tmp_path, sharing=False)
    doc = tomllib.loads(path.read_text())
    doc["associated"]["requires"] = {
        name: {"Domain": {"nominal": "documents", "kind": "identity"}}
        for name in ["source", "sink"]
    }
    write_manifest(doc, path)
    report = check_program(path, repo)
    assert all(
        "var" not in str(term)
        for term in report["associated_assumptions"]["terms"].values()
    )
