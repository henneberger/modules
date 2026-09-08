"""Associated type equations preserve rigid identity, kinds and finite terms."""

import pytest

from module_families.type_terms import (
    TypeTermError,
    collect_variables,
    normalize_constructors,
    normalize_term,
    substitute,
    term_id,
    unify,
)

REGISTRY = {"Vector": {"parameters": ["identity"], "result": "shared"},
            "Box": {"parameters": ["shared"], "result": "shared"}}


def nominal(name, kind="identity"):
    return {"nominal": name, "kind": kind}


def variable(name, kind="identity"):
    return {"var": name, "kind": kind}


def app(name, *args):
    return {"apply": name, "args": list(args), "kind": "shared"}


def test_constructor_normalization_is_defensive():
    registry = normalize_constructors(REGISTRY)
    registry["Vector"]["parameters"].append("linear")
    assert REGISTRY["Vector"]["parameters"] == ["identity"]
    term = app("Vector", nominal("space"))
    normalized = normalize_term(term, REGISTRY)
    normalized["args"][0]["nominal"] = "another"
    assert term["args"][0]["nominal"] == "space"


@pytest.mark.parametrize("term", [
    {}, [], {"nominal": "", "kind": "identity"},
    {"nominal": "a", "kind": "unknown"},
    {"nominal": "a", "kind": "identity", "extra": True},
    {"nominal": "a", "var": "a", "kind": "identity"},
    {"from": "slot", "kind": "identity"},
    {"from": "slot..Type", "kind": "identity"},
    app("Missing", nominal("a")), app("Vector"),
    app("Vector", nominal("a", "linear")),
    {"apply": "Vector", "args": [nominal("a")], "kind": "linear"},
    {"apply": "Vector", "args": (), "kind": "shared"},
])
def test_bad_terms_rejected(term):
    with pytest.raises(TypeTermError):
        normalize_term(term, REGISTRY)


@pytest.mark.parametrize("registry", [[], {"": {"parameters": [], "result": "shared"}},
    {"C": {"parameters": [], "result": "bad"}},
    {"C": {"parameters": "identity", "result": "shared"}},
    {"C": {"parameters": [], "result": "shared", "extra": True}},
])
def test_bad_constructor_registry(registry):
    with pytest.raises(TypeTermError):
        normalize_constructors(registry)


def test_substitution_resolves_paths_and_preserves_scopes():
    term = app("Vector", {"from": "embedding.Space", "kind": "identity"})
    assert substitute(term, {"embedding.Space": variable("local.Space"),
                             "local.Space": nominal("provider.Space")}, REGISTRY) == app("Vector", nominal("provider.Space"))
    assert substitute(variable("a.T"), {"b.T": nominal("X")}) == variable("a.T")


@pytest.mark.parametrize("bindings", [
    {"X": variable("X")},
    {"X": variable("Y"), "Y": variable("X")},
    {"X": nominal("x", "linear")},
])
def test_invalid_substitution(bindings):
    with pytest.raises(TypeTermError):
        substitute(variable("X"), bindings)


def test_substitution_occurs_under_constructor():
    with pytest.raises(TypeTermError, match="occurs"):
        substitute(variable("X", "shared"), {"X": app("Box", variable("X", "shared"))}, REGISTRY)


def test_congruence_solves_embedding_space():
    equations = [(app("Vector", variable("retrieval.Space")), app("Vector", nominal("model-v1"))),
                 (variable("index.Space"), variable("retrieval.Space"))]
    solution = unify(equations, REGISTRY)
    assert solution == {"index.Space": nominal("model-v1"), "retrieval.Space": nominal("model-v1")}
    assert all(substitute(a, solution, REGISTRY) == substitute(b, solution, REGISTRY) for a, b in equations)


def test_variable_representative_independent_of_equation_direction_and_order():
    equations = [(variable("Z"), variable("Y")), (variable("Y"), variable("A"))]
    expected = {"Y": variable("A"), "Z": variable("A")}
    assert unify(equations) == expected
    assert unify([(b, a) for a, b in reversed(equations)]) == expected


@pytest.mark.parametrize("equations,match", [
    ([(nominal("model-v1"), nominal("model-v2"))], "rigid"),
    ([(variable("X"), nominal("x", "linear"))], "kind mismatch"),
    ([(variable("X", "shared"), app("Box", variable("X", "shared")))], "occurs"),
    ([({"from": "one.Space", "kind": "identity"}, {"from": "two.Space", "kind": "identity"})], "rigid"),
    ([(variable("X"), variable("Y")), (variable("X", "linear"), nominal("a", "linear"))], "inconsistent kinds"),
])
def test_inconsistent_equations_have_context(equations, match):
    with pytest.raises(TypeTermError, match=f"equation .*{match}"):
        unify(equations, REGISTRY)


def test_rigid_constructor_names_never_structurally_unify():
    registry = dict(REGISTRY, OtherVector=REGISTRY["Vector"])
    with pytest.raises(TypeTermError, match="rigid"):
        unify([(app("Vector", nominal("X")), app("OtherVector", nominal("X")))], registry)


def test_variables_can_bind_rigid_paths_without_resolving_them():
    path = {"from": "store.Document", "kind": "identity"}
    assert unify([(variable("X"), path)]) == {"X": path}


def test_term_id_canonical_and_sensitive_to_nominal_identity():
    assert term_id(nominal("a")) == term_id({"kind": "identity", "nominal": "a"})
    assert term_id(nominal("a")) != term_id(nominal("b"))
    assert term_id(app("Vector", variable("X"))).startswith("type:")
    assert collect_variables(app("Box", app("Vector", variable("X")))) == {"X"}
    assert collect_variables({"from": "slot.X", "kind": "identity"}) == set()


def test_depth_and_size_bounds():
    term = nominal("x", "shared")
    for _ in range(33):
        term = app("Box", term)
    with pytest.raises(TypeTermError, match="depth"):
        normalize_term(term, REGISTRY)
    registry = {"Wide": {"parameters": ["identity"] * 1000, "result": "shared"}}
    with pytest.raises(TypeTermError, match="size"):
        normalize_term(app("Wide", *[nominal("x")] * 1000), registry)


def test_substitution_expansion_bounded():
    registry = {"Pair": {"parameters": ["shared", "shared"], "result": "shared"}}
    bindings = {f"X{i}": app("Pair", variable(f"X{i + 1}", "shared"), variable(f"X{i + 1}", "shared")) for i in range(10)}
    with pytest.raises(TypeTermError, match="bounds"):
        substitute(variable("X0", "shared"), bindings, registry)
