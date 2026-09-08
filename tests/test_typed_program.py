"""Separate checking must require contracts, never provider implementations."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_assemblies import publish

from module_families.interfaces import signature_from_spec
from module_families.manifest import load_manifest
from module_families.registry import Registry
from module_families.typed_program import TypeCheckError, build_program, check_program

ROOT = Path(__file__).resolve().parents[1]


def contract(*, parameters=None, returns=None, effects=None):
    types = deepcopy(
        load_manifest(ROOT / "families/transactions/interfaces.toml")["interfaces"][0][
            "typing"
        ]["types"]
    )
    types["Flag"] = {"id": "python.bool", "usage": "shared", "representation": "bool"}
    types["Ticket"] = {
        "id": "example.ticket",
        "usage": "affine",
        "representation": "opaque",
    }
    parameters = parameters or {}
    return {
        "id": "tests.workflow",
        "version": "1",
        "types": [],
        "callables": {
            "run": {
                "parameters": [
                    {"name": name, "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                    for name in parameters
                ]
            }
        },
        "typing": {
            "types": types,
            "operations": {
                "run": {
                    "parameters": parameters,
                    "returns": ["Text"] if returns is None else returns,
                    "effects": ["sqlite"] if effects is None else effects,
                }
            },
        },
    }


def prepare(tmp_path, source, *, output=None, store=None, allowed='["sqlite"]'):
    repository = Registry(tmp_path / "repository")
    store = (
        store
        or load_manifest(ROOT / "families/transactions/interfaces.toml")["interfaces"][
            0
        ]
    )
    output = output or contract()
    publish(tmp_path, repository, "contracts", interfaces=[store, output])
    (tmp_path / "program.mfl").write_text(source)
    manifest = tmp_path / "program.toml"
    manifest.write_text(f"""schema_version = 1
[family]
name = "checked-tests"
version = "1.0.0"
description = "Separately checked workflow tests"
[publisher]
name = "tests"
[module]
id = "workflow"
version = "1.0.0"
provides = {{id = "tests.workflow", version = "1"}}
[ports.store]
requires = {{id = "transactions.store", version = "1"}}
[program]
source = "program.mfl"
export = "run"
[policy]
allowed_effects = {allowed}
""")
    return repository, manifest


GOOD = 'session = store.begin()\nstore.put(session, "key", "value")\nresult = store.commit(session)\nreturn result\n'


def test_check_and_build_without_any_concrete_providers(tmp_path, monkeypatch):
    repository, manifest = prepare(tmp_path, GOOD)

    def forbidden(*args, **kwargs):
        raise AssertionError("separate compilation must never select providers")

    monkeypatch.setattr(repository, "versions", forbidden)
    monkeypatch.setattr(repository, "candidates", forbidden)
    report = check_program(manifest, repository)
    assert report["effects"] == ["sqlite"]
    assert report["guarantees"]["normal_path_usage"] is True
    assert report["guarantees"]["python_implementation_verified"] is False
    assert report["guarantees"]["exception_cleanup_verified"] is False
    built = build_program(manifest, repository, tmp_path / "built")
    assert built["member"] == "tests.workflow"
    assert Path(built["certificate"]).is_file()
    assert Path(built["index"]).is_file()


@pytest.mark.parametrize(
    "source,error",
    [
        (
            "session = store.begin()\nresult = store.commit(session)\nstore.abort(session)\nreturn result",
            "use after move",
        ),
        ('session = store.begin()\nreturn "leak"', "unconsumed linear"),
        (
            'session = store.begin()\nother = session\nstore.abort(session)\nreturn "bad"',
            "use after move",
        ),
        (
            'session = store.begin()\nresult = store.read("wrong", "key")\nstore.abort(session)\nreturn result',
            "nominal type mismatch",
        ),
        ('session = store.begin()\ndiscard(session)\nreturn "bad"', "only affine"),
        ('import os\nreturn "bad"', "Import"),
        ('for item in []:\n    pass\nreturn "bad"', "For"),
        ('result = getattr(store, "begin")\nreturn result', "declared port.operation"),
        ("result = store.__dict__\nreturn result", "values must be names"),
    ],
)
def test_rejects_usage_and_grammar_violations(tmp_path, source, error):
    repository, manifest = prepare(tmp_path, source)
    with pytest.raises(TypeCheckError, match=error):
        check_program(manifest, repository)


@pytest.mark.parametrize(
    "contract_effects,allowed,error",
    [
        ([], '["sqlite"]', "operation effect contract"),
        (["sqlite"], "[]", "program effect policy"),
    ],
)
def test_effect_bounds(tmp_path, contract_effects, allowed, error):
    repository, manifest = prepare(
        tmp_path, GOOD, output=contract(effects=contract_effects), allowed=allowed
    )
    with pytest.raises(TypeCheckError, match=error):
        check_program(manifest, repository)


def test_terminal_branch_consumes_resource_on_each_normal_path(tmp_path):
    output = contract(parameters={"keep": {"type": "Flag", "mode": "share"}})
    source = """session = store.begin()
if keep:
    result = store.commit(session)
    return result
else:
    store.abort(session)
    return "aborted"
"""
    repository, manifest = prepare(tmp_path, source, output=output)
    assert check_program(manifest, repository)["ir"][-1]["kind"] == "if"
    (tmp_path / "program.mfl").write_text(
        source.replace("    store.abort(session)\n", "")
    )
    with pytest.raises(TypeCheckError, match="unconsumed linear"):
        check_program(manifest, repository)


@pytest.mark.parametrize("source", ['discard(ticket)\nreturn "ok"', 'return "ok"'])
def test_affine_input_can_be_discarded_or_left_unused(tmp_path, source):
    output = contract(parameters={"ticket": {"type": "Ticket", "mode": "move"}})
    repository, manifest = prepare(tmp_path, source, output=output)
    assert check_program(manifest, repository)["effects"] == []


def test_same_nominal_identity_cannot_change_usage_between_interfaces(tmp_path):
    output = contract()
    output["typing"]["types"]["Session"]["usage"] = "affine"
    repository, manifest = prepare(tmp_path, GOOD, output=output)
    with pytest.raises(TypeCheckError, match="inconsistent nominal"):
        check_program(manifest, repository)


def test_two_moves_in_one_call_are_rejected(tmp_path):
    store = load_manifest(ROOT / "families/transactions/interfaces.toml")["interfaces"][
        0
    ]
    store["callables"]["merge"] = {
        "parameters": [
            {"name": name, "kind": "POSITIONAL_OR_KEYWORD", "required": True}
            for name in ("left", "right")
        ]
    }
    store["typing"]["operations"]["merge"] = {
        "parameters": {
            name: {"type": "Session", "mode": "move"} for name in ("left", "right")
        },
        "returns": ["Text"],
        "effects": ["sqlite"],
    }
    repository, manifest = prepare(
        tmp_path,
        "session = store.begin()\nresult = store.merge(session, session)\nreturn result",
        store=store,
    )
    with pytest.raises(TypeCheckError, match="duplicate move"):
        check_program(manifest, repository)


def test_documented_program_elaborates_to_working_ordinary_python(tmp_path):
    import runpy

    repository = Registry(tmp_path / "repository")
    interfaces = []
    for filename in ("interfaces.toml", "workflows.toml"):
        interfaces.extend(
            load_manifest(ROOT / "families/transactions" / filename)["interfaces"]
        )
    publish(tmp_path, repository, "contracts", interfaces=interfaces)
    manifest = ROOT / "examples/typed/transaction.toml"
    build_program(manifest, repository, tmp_path / "built")
    generated = runpy.run_path(str(tmp_path / "built/generated.py"))
    adapter = runpy.run_path(
        str(ROOT / "adaptations/transactions/src/transaction_adapters/sqlite.py")
    )
    store = signature_from_spec(interfaces[0]).seal(adapter["create"]())
    instance = generated["create"](store=store)
    assert instance["run"]("example", "stored value") == ("stored value", "committed")


def _generated_instance(tmp_path, repository, manifest):
    import runpy

    build_program(manifest, repository, tmp_path / "runtime-build")
    factory = runpy.run_path(str(tmp_path / "runtime-build/generated.py"))["create"]
    spec = repository.interface("transactions.store", "1")
    adapter = runpy.run_path(
        str(ROOT / "adaptations/transactions/src/transaction_adapters/sqlite.py")
    )
    return factory(store=signature_from_spec(spec).seal(adapter["create"]()))


def test_generated_resource_return_composes_with_checked_invocation(tmp_path):
    from module_families.ownership import Owned, invoke

    output = contract(
        parameters={"ticket": {"type": "Ticket", "mode": "move"}},
        returns=["Ticket"],
        effects=[],
    )
    repository, manifest = prepare(tmp_path, "return ticket", output=output)
    operation = _generated_instance(tmp_path, repository, manifest)["run"]
    spec = output["typing"]["types"]["Ticket"]
    original = Owned(object(), spec["id"], spec["usage"])
    (result,) = invoke(operation, [original], [{**spec, "mode": "move"}], [spec])
    assert not original.valid and result.valid
    assert result.type_id == original.type_id


def test_ignoring_affine_parameter_still_transfers_caller_ownership(tmp_path):
    from module_families.ownership import Owned

    output = contract(
        parameters={"ticket": {"type": "Ticket", "mode": "move"}}, effects=[]
    )
    repository, manifest = prepare(tmp_path, 'return "ok"', output=output)
    operation = _generated_instance(tmp_path, repository, manifest)["run"]
    original = Owned(object(), "example.ticket", "affine")
    assert operation(original) == "ok"
    assert not original.valid


def test_export_name_can_also_name_a_module_port(tmp_path):
    import runpy

    repository, manifest = prepare(tmp_path, GOOD.replace("store.", "run."))
    manifest.write_text(manifest.read_text().replace("ports.store", "ports.run"))
    build_program(manifest, repository, tmp_path / "built")
    factory = runpy.run_path(str(tmp_path / "built/generated.py"))["create"]
    adapter = runpy.run_path(
        str(ROOT / "adaptations/transactions/src/transaction_adapters/sqlite.py")
    )
    store = signature_from_spec(repository.interface("transactions.store", "1")).seal(
        adapter["create"]()
    )
    assert factory(run=store)["run"]() == "committed"


def test_primitive_identity_cannot_hide_effectful_truthiness(tmp_path):
    output = contract(
        parameters={"flag": {"type": "Flag", "mode": "share"}}, effects=[]
    )
    output["typing"]["types"]["Flag"]["representation"] = "opaque"
    repository, manifest = prepare(
        tmp_path, 'if flag:\n    return "yes"\nelse:\n    return "no"', output=output
    )
    with pytest.raises(TypeCheckError, match="canonical primitive"):
        check_program(manifest, repository)


def test_typed_build_bytes_do_not_depend_on_output_directory(tmp_path):
    import json

    repository, manifest = prepare(tmp_path, GOOD)
    first = build_program(manifest, repository, tmp_path / "one")
    second = build_program(manifest, repository, tmp_path / "two")
    a = json.loads(Path(first["index"]).read_text())
    b = json.loads(Path(second["index"]).read_text())
    assert a["members"][0]["sha256"] == b["members"][0]["sha256"]


def test_associated_result_requires_explicit_witness(tmp_path):
    output = contract(effects=[])
    output["associated"] = {"types": {"Space": "identity"}}
    repository, manifest = prepare(tmp_path, 'return "ok"', output=output)
    with pytest.raises(TypeCheckError, match="associated exports differ"):
        check_program(manifest, repository)
