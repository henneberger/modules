"""Typed metadata remains declarative across authoring and publication."""

from copy import deepcopy
from threading import Thread

import pytest

from module_families.compiler import build_family
from module_families.interfaces import (
    InterfaceError,
    signature_from_spec,
    validate_interface,
)
from module_families.manifest import load_manifest, write_manifest
from module_families.registry import Registry
from module_families.repository import RemoteRegistry, make_server


def typed_interface():
    return {
        "id": "example.transaction", "version": "1",
        "callables": {
            "commit": {"parameters": [
                {"name": "tx", "kind": "POSITIONAL_ONLY", "required": True},
                {"name": "value", "kind": "POSITIONAL_OR_KEYWORD", "required": True},
            ]}
        },
        "typing": {
            "types": {
                "Transaction": {"id": "example.tx", "usage": "linear", "representation": "opaque"},
                "Text": {"id": "example.text", "usage": "shared", "representation": "str"},
            },
            "operations": {"commit": {
                "parameters": {"tx": {"type": "Transaction", "mode": "move"},
                               "value": {"type": "Text", "mode": "share"}},
                "returns": ["Text"], "effects": ["write", "audit"],
            }},
        },
    }


def test_normalized_metadata_roundtrips_and_is_defensive():
    source = typed_interface()
    normalized = validate_interface(source)
    assert validate_interface(normalized) == normalized
    assert normalized["typing"]["operations"]["commit"]["effects"] == ["audit", "write"]
    runtime = signature_from_spec(source).metadata()
    assert "typing" not in runtime
    assert runtime == {k: v for k, v in normalized.items() if k != "typing"}
    normalized["typing"]["operations"]["commit"]["returns"].append("Transaction")
    normalized["typing"]["types"]["Text"]["id"] = "changed"
    assert source["typing"]["operations"]["commit"]["returns"] == ["Text"]
    assert source["typing"]["types"]["Text"]["id"] == "example.text"


@pytest.mark.parametrize("path,value", [
    (("typing",), None),
    (("typing", "unknown"), {}),
    (("typing", "types"), []),
    (("typing", "operations"), []),
    (("typing", "types", "bad name"), {}),
    (("typing", "types", "Text", "id"), " "),
    (("typing", "types", "Text", "usage"), []),
    (("typing", "types", "Text", "usage"), "unrestricted"),
    (("typing", "types", "Text", "representation"), "object"),
    (("typing", "types", "Text", "unexpected"), True),
    (("typing", "operations", "another"), {}),
    (("typing", "operations", "commit", "parameters"), {}),
    (("typing", "operations", "commit", "parameters", "tx", "mode"), "share"),
    (("typing", "operations", "commit", "parameters", "value", "mode"), "borrow"),
    (("typing", "operations", "commit", "parameters", "value", "type"), "Missing"),
    (("typing", "operations", "commit", "parameters", "value", "type"), []),
    (("typing", "operations", "commit", "returns"), ["Missing"]),
    (("typing", "operations", "commit", "returns"), "Text"),
    (("typing", "operations", "commit", "effects"), [""]),
    (("typing", "operations", "commit", "effects"), ["write", "write"]),
    (("typing", "operations", "commit", "effects"), [None]),
    (("callables", "commit", "asynchronous"), True),
    (("callables", "commit", "parameters", 1, "required"), False),
    (("callables", "commit", "parameters", 1, "kind"), "KEYWORD_ONLY"),
])
def test_invalid_typing_is_rejected_by_both_entrypoints(path, value):
    source = typed_interface()
    target = source
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    for validator in (validate_interface, signature_from_spec):
        with pytest.raises(InterfaceError):
            validator(source)


@pytest.mark.parametrize("usage,mode", [("affine", "move"), ("affine", "borrow"), ("linear", "borrow")])
def test_resource_modes(usage, mode):
    source = typed_interface()
    source["typing"]["types"]["Transaction"]["usage"] = usage
    source["typing"]["operations"]["commit"]["parameters"]["tx"]["mode"] = mode
    assert validate_interface(source)["typing"]


@pytest.mark.parametrize("kind", [
    "POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY",
    "VAR_POSITIONAL", "VAR_KEYWORD",
])
@pytest.mark.parametrize("required", [True, False])
def test_finite_parameter_shape_matrix(kind, required):
    source = typed_interface()
    source["callables"]["commit"]["parameters"] = [
        {"name": "tx", "kind": kind, "required": required}
    ]
    del source["typing"]["operations"]["commit"]["parameters"]["value"]
    if required and kind in {"POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD"}:
        assert validate_interface(source)
    else:
        with pytest.raises(InterfaceError):
            validate_interface(source)


@pytest.mark.parametrize("usage", ["shared", "affine", "linear"])
@pytest.mark.parametrize("mode", ["share", "move", "borrow"])
def test_finite_resource_mode_matrix(usage, mode):
    source = typed_interface()
    source["typing"]["types"]["Transaction"]["usage"] = usage
    source["typing"]["operations"]["commit"]["parameters"]["tx"]["mode"] = mode
    if (usage == "shared") == (mode == "share"):
        assert validate_interface(source)
    else:
        with pytest.raises(InterfaceError):
            validate_interface(source)


def test_missing_operation_and_inconsistent_nominal_aliases():
    source = typed_interface()
    source["typing"]["operations"].clear()
    with pytest.raises(InterfaceError, match="all callable"):
        validate_interface(source)
    source = typed_interface()
    alias = deepcopy(source["typing"]["types"]["Text"])
    source["typing"]["types"]["Alias"] = alias
    assert validate_interface(source)
    alias["usage"] = "linear"
    with pytest.raises(InterfaceError, match="inconsistent"):
        validate_interface(source)


def test_toml_build_and_repository_preserve_typed_contract(tmp_path):
    declaration = typed_interface()
    document = {
        "schema_version": 1, "publisher": {"name": "example"},
        "family": {"name": "typed", "version": "1.0.0", "description": "Typed contracts"},
        "interfaces": [declaration],
    }
    manifest = write_manifest(document, tmp_path / "family.toml")
    assert load_manifest(manifest)["interfaces"] == [validate_interface(declaration)]
    build_family(manifest, tmp_path / "build")
    repository = Registry(tmp_path / "repository")
    repository.publish(tmp_path / "build" / "index.json")
    published = repository.interface("example.transaction", "1")
    assert published == validate_interface(declaration)
    server = make_server(repository)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        remote = RemoteRegistry(f"http://127.0.0.1:{server.server_port}", cache=tmp_path / "cache")
        assert remote.interface("example.transaction", "1") == published
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
