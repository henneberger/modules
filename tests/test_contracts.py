import asyncio
import inspect
import json
import unittest

from module_families.contracts import (
    CallableSpec,
    ContractError,
    Functor,
    ModuleView,
    Requirement,
    Signature,
)


class Money:
    pass


class Receipt:
    pass


def charge_contract(amount, *, idempotency_key):
    raise AssertionError("contract prototypes must never run")


PROCESSOR = Signature(
    "payments.processor", "1", {"charge": charge_contract}, ("Money", "Receipt")
)


def make_processor(identity="local-a@1", money_type=Money):
    def charge(amount, *, idempotency_key):
        return identity, amount, idempotency_key

    return PROCESSOR.seal(
        {"charge": charge, "Money": money_type, "Receipt": Receipt, "secret": "hidden"},
        provides={"id": "payments.processor", "version": "1"},
        identity=identity,
    )


class CallableConformanceTests(unittest.TestCase):
    def test_validation_never_calls_prototype_or_candidate(self):
        def prototype(value, *, option=False):
            raise AssertionError("prototype invoked")

        def implementation(value, *, option=False, debug=False):
            raise AssertionError("implementation invoked")

        CallableSpec.from_callable(prototype).check(implementation)

    def test_extra_required_argument_rejected(self):
        def wrong(amount, account, *, idempotency_key):
            pass

        with self.assertRaisesRegex(ContractError, "rejects a contract call"):
            PROCESSOR.seal({"charge": wrong, "Money": Money, "Receipt": Receipt})

    def test_optional_contract_argument_cannot_become_required(self):
        def prototype(value, *, limit=10):
            pass

        def wrong(value, *, limit):
            pass

        with self.assertRaisesRegex(ContractError, "limit"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_optional_keyword_still_must_be_accepted(self):
        def prototype(value, *, first=1, second=2):
            pass

        def wrong(value, *, first=1):
            pass

        with self.assertRaisesRegex(ContractError, "second"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_keyword_contract_cannot_become_positional_only(self):
        def wrong(amount, /, *, idempotency_key):
            pass

        with self.assertRaisesRegex(ContractError, "amount"):
            CallableSpec.from_callable(charge_contract).check(wrong)

    def test_positional_contract_cannot_become_keyword_only(self):
        def wrong(*, amount, idempotency_key):
            pass

        with self.assertRaisesRegex(ContractError, "1 positional"):
            CallableSpec.from_callable(charge_contract).check(wrong)

    def test_later_optional_positional_only_parameter_cannot_accept_keyword(self):
        def prototype(*, value=0):
            pass

        def wrong(unused=0, value=0, /):
            pass

        with self.assertRaisesRegex(ContractError, "positional-only"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_positional_only_contract_allows_different_parameter_names(self):
        def prototype(amount, /, *, idempotency_key):
            pass

        def implementation(value, /, *, idempotency_key):
            pass

        CallableSpec.from_callable(prototype).check(implementation)

    def test_parameter_reordering_that_causes_double_binding_rejected(self):
        def prototype(first=1, second=2):
            pass

        def wrong(second=2, first=1):
            pass

        with self.assertRaisesRegex(ContractError, "multiple values"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_variadic_capacity_and_colliding_keywords(self):
        def prototype(*args, **kwargs):
            pass

        def valid(*values, **options):
            pass

        def finite(first=None, **kwargs):
            pass

        def collision(first=None, *args, **kwargs):
            pass

        spec = CallableSpec.from_callable(prototype)
        spec.check(valid)
        with self.assertRaisesRegex(ContractError, "arbitrary positional"):
            spec.check(finite)
        with self.assertRaisesRegex(ContractError, "multiple values"):
            spec.check(collision)

    def test_positional_only_name_can_also_be_an_extra_keyword(self):
        def prototype(value, /, **kwargs):
            pass

        def wrong(value, **kwargs):
            pass

        with self.assertRaisesRegex(ContractError, "multiple values"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_arbitrary_keywords_require_variadic_provider(self):
        def prototype(**kwargs):
            pass

        def wrong(option=None):
            pass

        with self.assertRaisesRegex(ContractError, "arbitrary keyword"):
            CallableSpec.from_callable(prototype).check(wrong)

    def test_async_function_and_callable_object(self):
        async def prototype(value):
            pass

        class AsyncOperation:
            async def __call__(self, value):
                return value + 1

        spec = CallableSpec.from_callable(prototype)
        operation = AsyncOperation()
        spec.check(operation)
        self.assertEqual(asyncio.run(operation(2)), 3)
        with self.assertRaisesRegex(ContractError, "must be async"):
            spec.check(lambda value: value)
        with self.assertRaisesRegex(ContractError, "must be sync"):
            CallableSpec.from_callable(lambda value: value).check(operation)

    def test_async_generators_explicitly_unsupported(self):
        async def generator(value):
            yield value

        with self.assertRaisesRegex(ContractError, "async generator"):
            CallableSpec.from_callable(generator)

    def test_metadata_never_formats_defaults_or_evaluates_annotations(self):
        class NotPrintable:
            def __repr__(self):
                raise AssertionError("default repr was invoked")

        def prototype(value: "undefined_annotation" = NotPrintable()):  # noqa: F821, B008 - deliberately unevaluable metadata.
            pass

        spec = CallableSpec.from_callable(prototype)
        metadata = json.loads(json.dumps(spec.metadata()))
        self.assertFalse(metadata["parameters"][0]["required"])
        spec.check(prototype)

    def test_uninspectable_callable_and_noncallable_report_contract_error(self):
        spec = CallableSpec(inspect.Signature())
        with self.assertRaisesRegex(ContractError, "callable"):
            spec.check(42)
        with self.assertRaisesRegex(ContractError, "inspectable"):
            spec.check(type)


class ModuleAndCompositionTests(unittest.TestCase):
    def test_sealed_view_projects_exports_and_copies_mapping(self):
        original = {
            "charge": charge_contract,
            "Money": Money,
            "Receipt": Receipt,
            "secret": 1,
        }
        view = PROCESSOR.seal(original)
        original["Money"] = str
        self.assertIs(view.Money, Money)
        self.assertEqual(set(view), {"charge", "Money", "Receipt"})
        self.assertNotIn("secret", view)
        with self.assertRaises(AttributeError):
            _ = view.secret
        with self.assertRaises(TypeError):
            view["Money"] = str
        with self.assertRaises(AttributeError):
            view.Money = str
        with self.assertRaises(AttributeError):
            del view._exports
        self.assertIsInstance(view, ModuleView)

    def test_missing_exports_and_wrong_type_exports(self):
        with self.assertRaisesRegex(ContractError, "missing callable"):
            PROCESSOR.seal({"Money": Money, "Receipt": Receipt})
        with self.assertRaisesRegex(ContractError, "missing type"):
            PROCESSOR.seal({"charge": charge_contract})
        with self.assertRaisesRegex(ContractError, "Python type object"):
            PROCESSOR.seal(
                {"charge": charge_contract, "Money": Money(), "Receipt": Receipt}
            )

    def test_wrong_declared_contract_and_version_rejected(self):
        exports = dict(make_processor())
        for reference in (
            {"id": "payments.refund", "version": "1"},
            {"id": "payments.processor", "version": "2"},
        ):
            with self.assertRaisesRegex(ContractError, "provider declares"):
                PROCESSOR.seal(exports, provides=reference)
        other = Signature(
            "payments.refund", "1", PROCESSOR.callables, PROCESSOR.types
        ).seal(exports)
        with self.assertRaisesRegex(ContractError, "provides.*requires"):
            Requirement(PROCESSOR).check(other, "processor")

    def test_nominal_type_constraint_rejects_same_named_class(self):
        OtherMoney = type("Money", (), {})
        requirement = Requirement(PROCESSOR, types={"Money": Money})
        requirement.check(make_processor())
        with self.assertRaisesRegex(ContractError, "nominal type 'Money'"):
            requirement.check(make_processor(money_type=OtherMoney), "processor")

    def test_dishonest_same_id_signature_cannot_skip_binding_shape_check(self):
        def wrong(amount, extra_required, *, idempotency_key):
            pass

        looser = Signature(
            PROCESSOR.id, PROCESSOR.version, {"charge": wrong}, PROCESSOR.types
        )
        provider = looser.seal({"charge": wrong, "Money": Money, "Receipt": Receipt})
        with self.assertRaisesRegex(ContractError, "extra_required"):
            Requirement(PROCESSOR).check(provider)

    def test_composition_checks_all_bindings_and_sharing_before_factory(self):
        invocations = []

        def factory(*, processor, ledger):
            invocations.append((processor, ledger))
            return {
                "charge": processor.charge,
                "Money": processor.Money,
                "Receipt": processor.Receipt,
            }

        checkout = Functor(
            "checkout@1",
            {"processor": Requirement(PROCESSOR), "ledger": Requirement(PROCESSOR)},
            PROCESSOR,
            factory,
            sharing=(
                ("processor.Money", "ledger.Money"),
                ("processor.Receipt", "ledger.Receipt"),
            ),
        )
        processor = make_processor()
        with self.assertRaisesRegex(ContractError, "missing=.*ledger"):
            checkout(processor=processor)
        with self.assertRaisesRegex(ContractError, "extra=.*unknown"):
            checkout(processor=processor, ledger=processor, unknown=processor)
        with self.assertRaisesRegex(ContractError, "sealed ModuleView"):
            checkout(processor=processor, ledger=dict(processor))
        with self.assertRaisesRegex(ContractError, "type sharing conflict"):
            checkout(processor=processor, ledger=make_processor(money_type=str))
        self.assertEqual(invocations, [])
        result = checkout(processor=processor, ledger=processor)
        self.assertEqual(len(invocations), 1)
        self.assertIs(result.Money, Money)
        self.assertEqual(result.charge(7, idempotency_key="k"), ("local-a@1", 7, "k"))

    def test_factory_always_runs_and_runtime_state_is_not_cached(self):
        instances = []

        def read_contract():
            pass

        result_signature = Signature("counter", "1", {"read": read_contract})

        def factory(*, processor):
            state = []
            instances.append(state)

            def read():
                state.append(None)
                return len(state)

            return {"read": read}

        instantiate = Functor(
            "counter@1",
            {"processor": Requirement(PROCESSOR)},
            result_signature,
            factory,
        )
        processor = make_processor()
        first = instantiate(processor=processor)
        second = instantiate(processor=processor)
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(first.read(), 1)
        self.assertEqual(first.read(), 2)
        self.assertEqual(second.read(), 1)
        self.assertEqual(len(instances), 2)
        different = instantiate(processor=make_processor(identity="local-b@1"))
        self.assertNotEqual(first.identity, different.identity)

    def test_invalid_factory_result_checked(self):
        factory = Functor("bad@1", {}, PROCESSOR, lambda: {})
        with self.assertRaisesRegex(ContractError, "missing callable"):
            factory()

    def test_invalid_declarations_fail_early(self):
        with self.assertRaisesRegex(ContractError, "distinct"):
            Signature("x", "1", {"Money": lambda: None}, ("Money",))
        with self.assertRaisesRegex(ContractError, "not a declared type"):
            Requirement(PROCESSOR, types={"Token": str})
        with self.assertRaisesRegex(ContractError, "dependency slots"):
            Functor(
                "x",
                {"processor": Requirement(PROCESSOR)},
                PROCESSOR,
                lambda required: {},
            )
        with self.assertRaisesRegex(ContractError, "not a declared dependency type"):
            Functor(
                "x",
                {"processor": Requirement(PROCESSOR)},
                PROCESSOR,
                lambda **kwargs: {},
                sharing=(("processor.Money", "processor.unknown"),),
            )

    def test_metadata_is_json_serializable_and_copies_do_not_mutate_contract(self):
        view = make_processor()
        reference = view.provides
        reference["id"] = "changed"
        self.assertEqual(view.provides["id"], PROCESSOR.id)
        factory = Functor(
            "x",
            {"processor": Requirement(PROCESSOR, {"Money": Money})},
            PROCESSOR,
            lambda processor: processor,
        )
        for metadata in (PROCESSOR.metadata(), view.metadata(), factory.metadata()):
            self.assertEqual(json.loads(json.dumps(metadata)), metadata)
        self.assertEqual(
            factory.metadata()["parameters"]["processor"]["fixed_types"], ["Money"]
        )


if __name__ == "__main__":
    unittest.main()
