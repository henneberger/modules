"""Runtime interpretation of concrete planner expressions over verified values."""

import hashlib
import unittest

from module_families.composition import CompositionError, link_expression
from module_families.contracts import ContractError, Signature
from module_families.planning import PlanningError, plan, select


def no_arguments():
    raise AssertionError("prototype invoked")


def unary(value):
    raise AssertionError("prototype invoked")


def card(alias, signature, *, kind="operation", requires=None, **extra):
    return {
        "family": "composition-tests",
        "id": alias,
        "version": "1.0",
        "sha256": hashlib.sha256(alias.encode()).hexdigest(),
        "kind": kind,
        "provides": signature.reference(),
        "requires": {
            name: value.reference() for name, value in (requires or {}).items()
        },
        "effects": [],
        **extra,
    }


def signatures(*values):
    return {(value.id, value.version): value for value in values}


class CompositionTests(unittest.TestCase):
    def test_closed_raw_callable_has_an_explicit_single_export_interpretation(self):
        signature = Signature("unary", "1", {"apply": unary})
        expression = {"use": "increment"}
        candidates = {"increment": card("increment", signature)}
        result = link_expression(
            expression,
            {"increment": lambda value: value + 1},
            candidates,
            signatures(signature),
        )
        self.assertEqual(result.apply(4), 5)
        self.assertEqual(result.provides, signature.reference())

    def test_closed_raw_type_preserves_object_identity(self):
        class Token:
            pass

        signature = Signature("token", "1", types=("Token",))
        result = link_expression(
            {"use": "token"},
            {"token": Token},
            {"token": card("token", signature, kind="type")},
            signatures(signature),
            types={"Token": Token},
        )
        self.assertIs(result.Token, Token)

    def test_each_repeated_factory_occurrence_gets_fresh_state(self):
        counter = Signature("counter", "1", {"next": no_arguments})
        pair = Signature("pair", "1", {"next_pair": no_arguments})
        calls = []

        def make_counter():
            state = []
            calls.append(state)

            def next_value():
                state.append(None)
                return len(state)

            return {"next": next_value}

        def make_pair(*, left, right):
            return {"next_pair": lambda: (left.next(), right.next())}

        candidates = {
            "counter": card("counter", counter, kind="module-factory"),
            "pair": card(
                "pair",
                pair,
                kind="functor",
                requires={"left": counter, "right": counter},
            ),
        }
        expression = {
            "use": "pair",
            "with": {"left": {"use": "counter"}, "right": {"use": "counter"}},
        }
        concrete = select(plan(expression, candidates))["expression"]
        exports = {"counter": make_counter, "pair": make_pair}
        first = link_expression(
            concrete, exports, candidates, signatures(counter, pair)
        )
        self.assertEqual(first.next_pair(), (1, 1))
        self.assertEqual(first.next_pair(), (2, 2))
        second = link_expression(
            concrete, exports, candidates, signatures(counter, pair)
        )
        self.assertEqual(second.next_pair(), (1, 1))
        self.assertEqual(len(calls), 4)
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.instance_id, second.instance_id)

    def test_nominal_library_constraints_apply_to_every_intermediate_result(self):
        class Token:
            pass

        class WrongToken:
            pass

        typed = Signature("typed", "1", {"next": no_arguments}, ("Token",))
        parent_calls = []

        def passthrough(*, child):
            parent_calls.append(child)
            return dict(child)

        candidates = {
            "leaf": card("leaf", typed, kind="module-factory"),
            "parent": card("parent", typed, kind="functor", requires={"child": typed}),
        }
        expression = {"use": "parent", "with": {"child": {"use": "leaf"}}}
        exports = {
            "leaf": lambda: {"next": lambda: 1, "Token": WrongToken},
            "parent": passthrough,
        }
        with self.assertRaisesRegex(ContractError, "nominal type"):
            link_expression(
                expression,
                exports,
                candidates,
                signatures(typed),
                types={"Token": Token},
            )
        self.assertEqual(parent_calls, [])
        exports["leaf"] = lambda: {"next": lambda: 1, "Token": Token}
        result = link_expression(
            expression, exports, candidates, signatures(typed), types={"Token": Token}
        )
        self.assertIs(result.Token, Token)
        self.assertEqual(len(parent_calls), 1)

    def test_false_manifest_type_equality_is_rechecked_with_actual_type_objects(self):
        left_type = type("Token", (), {})
        right_type = type("Token", (), {})
        typed = Signature("typed", "1", types=("Token",))
        empty = Signature("empty", "1")
        calls = []

        def combine(*, left, right):
            calls.append(None)
            return {}

        candidates = {
            "left": card(
                "left", typed, kind="type", type_exports={"Token": "claimed-same"}
            ),
            "right": card(
                "right", typed, kind="type", type_exports={"Token": "claimed-same"}
            ),
            "combine": card(
                "combine",
                empty,
                kind="functor",
                requires={"left": typed, "right": typed},
                sharing=[["left.Token", "right.Token"]],
            ),
        }
        expression = {
            "use": "combine",
            "with": {"left": {"use": "left"}, "right": {"use": "right"}},
        }
        self.assertEqual(plan(expression, candidates)["status"], "unique")
        with self.assertRaisesRegex(ContractError, "type sharing conflict"):
            link_expression(
                expression,
                {"left": left_type, "right": right_type, "combine": combine},
                candidates,
                signatures(typed, empty),
            )
        self.assertEqual(calls, [])

    def test_preflight_missing_export_or_signature_runs_no_factory(self):
        leaf = Signature("leaf", "1", {"next": no_arguments})
        other = Signature("other", "1", {"next": no_arguments})
        empty = Signature("empty", "1")
        calls = []

        def make_leaf():
            calls.append(None)
            return {"next": lambda: 1}

        candidates = {
            "a": card("a", leaf, kind="module-factory"),
            "z": card("z", other, kind="module-factory"),
            "parent": card(
                "parent", empty, kind="functor", requires={"a": leaf, "z": other}
            ),
        }
        expression = {"use": "parent", "with": {"a": {"use": "a"}, "z": {"use": "z"}}}
        exports = {"a": make_leaf, "parent": lambda **kwargs: {}}
        with self.assertRaisesRegex(CompositionError, "no verified loaded export"):
            link_expression(
                expression, exports, candidates, signatures(leaf, other, empty)
            )
        exports["z"] = make_leaf
        with self.assertRaisesRegex(CompositionError, "missing runtime Signature"):
            link_expression(expression, exports, candidates, signatures(leaf, empty))
        self.assertEqual(calls, [])

    def test_holes_are_rejected_even_when_metadata_has_one_solution(self):
        signature = Signature("counter", "1", {"next": no_arguments})
        candidates = {"only": card("only", signature, kind="module-factory")}
        expression = {"hole": "implementation", "requires": signature.reference()}
        self.assertEqual(plan(expression, candidates)["status"], "unique")
        with self.assertRaisesRegex(CompositionError, "concrete tree"):
            link_expression(
                expression, {"only": lambda: {}}, candidates, signatures(signature)
            )

    def test_raw_multi_export_value_requires_explicit_factory_declaration(self):
        signature = Signature(
            "pair", "1", {"first": no_arguments, "second": no_arguments}
        )
        calls = []

        def factory():
            calls.append(None)
            return {"first": lambda: 1, "second": lambda: 2}

        candidates = {"pair": card("pair", signature)}
        with self.assertRaisesRegex(
            CompositionError, "explicitly declare a module-factory"
        ):
            link_expression(
                {"use": "pair"}, {"pair": factory}, candidates, signatures(signature)
            )
        self.assertEqual(calls, [])
        candidates["pair"]["kind"] = "module-factory"
        result = link_expression(
            {"use": "pair"}, {"pair": factory}, candidates, signatures(signature)
        )
        self.assertEqual((result.first(), result.second()), (1, 2))

    def test_factory_shape_and_raw_provider_shape_checked_before_invocation(self):
        signature = Signature("counter", "1", {"next": no_arguments})
        candidates = {"factory": card("factory", signature, kind="module-factory")}
        with self.assertRaisesRegex(ContractError, "dependency slots"):
            link_expression(
                {"use": "factory"},
                {"factory": lambda required: {}},
                candidates,
                signatures(signature),
            )
        candidates["factory"]["kind"] = "operation"
        with self.assertRaisesRegex(ContractError, "required"):
            link_expression(
                {"use": "factory"},
                {"factory": lambda required: None},
                candidates,
                signatures(signature),
            )

    def test_declared_incompatibility_fails_planning_before_runtime(self):
        required = Signature("counter", "1", {"next": no_arguments})
        wrong = Signature("counter", "2", {"next": no_arguments})
        candidates = {
            "wrong": card("wrong", wrong, kind="module-factory"),
            "parent": card(
                "parent", required, kind="functor", requires={"child": required}
            ),
        }
        with self.assertRaises(PlanningError):
            link_expression(
                {"use": "parent", "with": {"child": {"use": "wrong"}}},
                {},
                candidates,
                signatures(required, wrong),
            )

    def test_signature_lookup_must_match_its_declared_key(self):
        declared = Signature("counter", "1", {"next": no_arguments})
        wrong = Signature("counter", "2", {"next": no_arguments})
        with self.assertRaisesRegex(CompositionError, "declares another contract"):
            link_expression(
                {"use": "x"},
                {"x": lambda: 1},
                {"x": card("x", declared)},
                {("counter", "1"): wrong},
            )

    def test_result_mapping_and_unsupported_async_factory_are_checked(self):
        signature = Signature("counter", "1", {"next": no_arguments})
        candidates = {"x": card("x", signature, kind="module-factory")}
        with self.assertRaisesRegex(ContractError, "exports must be a mapping"):
            link_expression(
                {"use": "x"}, {"x": lambda: None}, candidates, signatures(signature)
            )

        async def async_factory():
            return {}

        with self.assertRaisesRegex(ContractError, "synchronous"):
            link_expression(
                {"use": "x"}, {"x": async_factory}, candidates, signatures(signature)
            )

    def test_cyclic_expression_objects_and_invalid_fixed_types_are_rejected(self):
        expression = {"use": "x", "with": {}}
        expression["with"]["self"] = expression
        with self.assertRaisesRegex(CompositionError, "finite JSON tree"):
            link_expression(expression, {}, {}, {})
        with self.assertRaisesRegex(CompositionError, "Python type objects"):
            link_expression({"use": "x"}, {}, {}, {}, types={"Token": "not a type"})


if __name__ == "__main__":
    unittest.main()
