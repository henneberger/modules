"""Associated witnesses compose without constructing implementations."""
from copy import deepcopy

import pytest

from module_families.associated import (
    resolve_metadata,
    validate_against_interface,
    validate_associated,
)
from module_families.composition import link_expression
from module_families.contracts import ContractError, Functor, Requirement, Signature
from module_families.planning import plan


def nominal(name, kind="identity"):
    return {"nominal": name, "kind": kind}


def path(name, kind="identity"):
    return {"from": name, "kind": kind}


SPACE = nominal("embedding.model.v1")
VECTOR = {"parameters": ["identity"], "result": "shared"}


def card(name, associated=None, requires=None):
    return {"family": "kb", "id": name, "version": "1", "sha256": "a" * 64,
            "provides": {"id": "kb.provider", "version": "1"}, "kind": "module",
            "effects": [], "requires": requires or {}, "associated": associated or {}}


def test_substitution_preserves_nested_constructor_and_registry():
    source = {"types": {"Space": SPACE}, "constructors": {"kb.Vector": VECTOR}}
    consumer = card("consumer", {"types": {"Vector": {"apply": "kb.Vector", "args": [path("embed.Space")], "kind": "shared"}}, "constructors": {"kb.Vector": VECTOR}}, {"embed": {"id": "kb.provider", "version": "1"}})
    result = resolve_metadata(consumer, {"embed": source})
    assert result == {"types": {"Vector": {"apply": "kb.Vector", "args": [SPACE], "kind": "shared"}}, "constructors": {"kb.Vector": VECTOR}}
    assert consumer["associated"]["types"]["Vector"]["args"] == [path("embed.Space")]


def test_constructor_registry_conflict_rejected():
    with pytest.raises(ValueError, match="constructor conflict"):
        resolve_metadata(card("x", {"constructors": {"kb.Vector": VECTOR}}), {"source": {"types": {}, "constructors": {"kb.Vector": {"parameters": [], "result": "shared"}}}})


@pytest.mark.parametrize("term", [path("missing.Space"), {"var": "Space", "kind": "identity"}, {"apply": "unknown", "kind": "shared", "args": []}])
def test_invalid_member_terms_rejected(term):
    with pytest.raises(ValueError):
        validate_associated(card("x", {"types": {"Space": term}}))


def test_missing_witness_rejected():
    consumer = card("x", {"types": {"Space": path("source.Space")}}, {"source": {"id": "kb.provider", "version": "1"}})
    with pytest.raises(ValueError, match="missing associated"):
        resolve_metadata(consumer, {"source": {"types": {}, "constructors": {}}})


def test_kind_mismatch_rejected_during_substitution():
    consumer = card("x", {"types": {"Space": path("source.Space", "linear")}}, {"source": {"id": "kb.provider", "version": "1"}})
    with pytest.raises(ValueError, match="kind"):
        resolve_metadata(consumer, {"source": {"Space": SPACE}})


def test_module_metadata_defensively_copied():
    signature = Signature("kb.provider", "1")
    metadata = {"types": {"Space": SPACE}}
    module = signature.seal({}, associated=metadata)
    metadata["types"]["Space"] = nominal("mutated")
    output = module.metadata()
    output["associated"]["types"].clear()
    assert module.metadata()["associated"]["types"] == {"Space": SPACE}


def test_module_view_rejects_open_witness():
    with pytest.raises(ContractError):
        Signature("kb.provider", "1").seal({}, associated={"types": {"Space": path("source.Space")}})


def test_functor_checks_associated_sharing_before_factory_side_effect():
    signature = Signature("kb.provider", "1")
    calls = []
    associated = {"types": {"Space": path("left.Space")}, "sharing": [[path("left.Space"), path("right.Space")]]}
    factory = Functor("join", {"left": Requirement(signature), "right": Requirement(signature)}, signature,
                      lambda **bindings: calls.append(bindings) or {}, associated=associated)
    left = signature.seal({}, associated={"types": {"Space": SPACE}})
    wrong = signature.seal({}, associated={"types": {"Space": nominal("other.model")}})
    with pytest.raises(ContractError, match="sharing mismatch"):
        factory(left=left, right=wrong)
    assert calls == []
    result = factory(left=left, right=left)
    assert result.metadata()["associated"]["types"] == {"Space": SPACE}
    assert len(calls) == 1


def _composition():
    provider = card("provider", {"types": {"Space": SPACE}})
    other = card("other", {"types": {"Space": nominal("other.model")}})
    consumer = card("consumer", {"types": {"Space": path("embed.Space")}, "requires": {"index": {"Space": path("embed.Space")}}}, {"embed": provider["provides"], "index": provider["provides"]})
    consumer["kind"] = "functor"
    return {"provider": provider, "other": other, "consumer": consumer}


def test_planning_rejects_concrete_witness_mismatch():
    candidates = _composition()
    expression = {"use": "consumer", "with": {"embed": {"use": "provider"}, "index": {"use": "other"}}}
    result = plan(expression, candidates)
    assert result["status"] == "unsatisfied"
    assert result["rejections"][0]["code"] == "associated-type-mismatch"
    expression["with"]["index"] = {"use": "provider"}
    result = plan(expression, candidates)
    assert result["solutions"][0]["associated"]["types"] == {"Space": SPACE}


def test_linking_preserves_associated_witnesses():
    candidates = _composition()
    expression = {"use": "consumer", "with": {"embed": {"use": "provider"}, "index": {"use": "provider"}}}
    calls = []
    def create(*, embed, index):
        assert embed.metadata()["associated"] == index.metadata()["associated"]
        calls.append(True)
        return {}
    module = link_expression(expression, {"provider": {}, "consumer": create}, candidates, {("kb.provider", "1"): Signature("kb.provider", "1")})
    assert calls == [True]
    assert module.metadata()["associated"]["types"] == {"Space": SPACE}


def test_linking_mismatch_fails_before_any_factory():
    candidates = _composition()
    candidates["provider"]["kind"] = "module-factory"
    expression = {"use": "consumer", "with": {"embed": {"use": "provider"}, "index": {"use": "other"}}}
    calls = []
    with pytest.raises(ValueError):
        link_expression(expression, {"provider": lambda: calls.append(True) or {}, "other": {}, "consumer": lambda **args: {}}, candidates, {("kb.provider", "1"): Signature("kb.provider", "1")})
    assert not calls


@pytest.mark.parametrize("types", [{}, {"Space": nominal("wrong", "shared")}, {"Space": SPACE, "Extra": SPACE}])
def test_interface_export_names_and_kinds_required(types):
    with pytest.raises(ValueError, match="associated"):
        validate_against_interface(card("provider", {"types": types}), {"associated": {"types": {"Space": "identity"}}})


def test_interface_validation_does_not_mutate():
    provider = card("provider", {"types": {"Space": SPACE}})
    snapshot = deepcopy(provider)
    validate_against_interface(provider, {"associated": {"types": {"Space": "identity"}}})
    assert provider == snapshot


def test_published_witness_reaches_locked_assembly_and_offline_verification(tmp_path):
    from module_families.assemblies import (
        instantiate,
        lock_assembly,
        resolve_assembly,
        verify_assembly,
    )
    from module_families.compiler import build_family
    from module_families.manifest import write_manifest
    from module_families.registry import Registry

    root = tmp_path / "project"
    package = root / "src" / "associated_fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def run(value):\n    return value\n")
    reference = {"id": "fixture.associated", "version": "1"}
    spec = {**reference, "types": [], "callables": {"run": {"asynchronous": False, "parameters": [{"name": "value", "kind": "POSITIONAL_OR_KEYWORD", "required": True}]}}, "associated": {"types": {"Space": "identity"}}}
    manifest = {"schema_version": 1, "name": "associated-fixture", "version": "1.0.0", "description": "Associated fixture", "publisher": "tests", "context": {},
                "source": {"root": "src", "package": "associated_fixture"}, "interfaces": [spec],
                "members": [{"id": "valid", "version": "1.0.0", "kind": "operation", "summary": "Valid witness", "symbol": "associated_fixture:run", "provides": reference, "effects": [], "associated": {"types": {"Space": SPACE}}},
                            {"id": "missing", "version": "1.0.0", "kind": "operation", "summary": "Missing witness", "symbol": "associated_fixture:run", "provides": reference, "effects": []}]}
    write_manifest(manifest, root / "family.toml")
    build_family(root / "family.toml", root / "dist")
    repository = Registry(tmp_path / "repository")
    repository.publish(root / "dist" / "index.json")
    request = {"schema_version": 1, "assembly": {"name": "associated-test", "type_libraries": []}, "bindings": {"selected": {"requires": reference}}, "expression": {"use": "selected"}, "policy": {}}
    resolution = resolve_assembly(request, repository)
    assert resolution["status"] == "unique"
    assert any("associated exports" in rejection.get("error", "") for rejection in resolution["rejections"])
    lock = lock_assembly(resolution, repository)
    verify_assembly(lock)
    instance = instantiate(lock, repository, tmp_path / "installed")
    assert instance.module.metadata()["associated"]["types"] == {"Space": SPACE}
    assert instance.run("works") == "works"
