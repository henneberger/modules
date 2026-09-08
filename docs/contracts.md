# Runtime module contracts

Version 0.4.0 adds optional interface `typing` metadata for nominal value types, shared/affine/linear usage, parameter modes, results, and operation effects. [Checked `.mfl` programs](checked-language.md) consume those declarations at build time. The ordinary runtime `Signature` described below continues to check call shapes and nominal Python type exports; it does not itself become a general value type checker. Generated checked operations use separate ownership guards.

Version 0.3.0 also supports declared semantic indices with `Signature.seal(..., indices={"Space": "identity"})`. They appear in `ModuleView.metadata()["indices"]`. Repository composition checks member `index_requires`, `index_sharing`, and `index_exports`, and preserves resolved indices on results. These are assertions about compatibility, separate from live nominal type equality. See the [module build guide](module-build.md).

A family groups software by problem domain. A signature defines an interface under a specific contract ID and version. Only a provider satisfying the required signature is eligible for a module binding; being in the payment family is insufficient.

```python
from module_families.contracts import Signature, Requirement, Functor

def charge_interface(amount, *, idempotency_key):
    raise NotImplementedError("prototype; never invoked during validation")

processor_signature = Signature(
    "payments.processor", "1",
    callables={"charge": charge_interface},
    types=("Money", "Receipt"),
)
provider = processor_signature.seal(
    {"charge": charge, "Money": Money, "Receipt": Receipt},
    provides={"id": "payments.processor", "version": "1"},
    identity="local.alpha@1",
)
```

`Signature.seal()` checks required exports, then returns a `ModuleView`, a read-only `Mapping` containing only declared exports. Use `provider["charge"]` or `provider.charge`. Dictionary access also works for export names that collide with view methods such as `items`. Exported values themselves may remain mutable. The view copies the supplied mapping, so later mapping changes do not change which objects it exposes.

`provides` declares an exact contract ID/version. An explicit mismatch is rejected. When omitted, the act of sealing declares the provided signature. Matching IDs alone are insufficient: dependency binding rechecks actual exports against the consumer's signature, including when another provider constructed a different signature with the same ID.

## Call compatibility

Callable prototypes describe the calls a consumer may make. `CallableSpec.from_callable(prototype)` constructs the specification explicitly; `Signature` performs the same conversion when passed a callable. `CallableSpec(inspect.Signature(...), asynchronous=False)` supports directly constructed declarations.

Validation uses `inspect.signature(..., follow_wrapped=False, eval_str=False)` and argument binding; it does not invoke the prototype or candidate. It checks that the provider accepts every argument-binding shape permitted by the contract, including positional-only and keyword-only parameters, optional arguments, and variadics. A required extra argument, a newly required optional argument, a missing optional keyword, or a positional/keyword collision is incompatible. A provider can accept additional optional arguments or more permissive variadics.

The checker considers each positional prefix and probes required and optional keywords. Keyword name collisions are checked individually, including collisions between arbitrary `**kwargs` and provider positional parameters. It requires variadic provider capacity when the contract permits unbounded arguments. This checks Python's signature-binding rules; it is not an analysis of the callable body.

Synchronous and coroutine functions are distinguished. Async callable objects are supported when their `__call__` is async. A synchronous function returning an awaitable is not accepted as an async implementation; async generators are unsupported. Some extension/builtin callables lack inspectable signatures and fail with `ContractError`. Signature overrides and arbitrary callable behavior can lie about acceptance. Introspection itself is ordinary Python and is not a security boundary.

Annotations, accepted value ranges, return values, exception behavior, idempotency, and effects are not checked. A prototype's default value does not impose the same provider default; only whether the argument may be omitted is enforced. Behavioral requirements need tests or stronger separately implemented analysis.

## Explicit module parameters and type sharing

```python
checkout = Functor(
    name="payments.checkout@1",
    parameters={
        "processor": Requirement(processor_signature, types={"Money": Money}),
    },
    result=checkout_signature,
    factory=lambda processor: {
        "charge": processor.charge,
        "Money": processor.Money,
        "Receipt": processor.Receipt,
    },
)
application = checkout(processor=provider)
```

The factory's Python call signature must accept every named dependency slot. Factories are synchronous and return an export mapping. Invocation rejects missing or extra slots, checks all provider contracts and nominal constraints, and only then invokes the factory. The result is validated and sealed under the result signature. Factory exceptions propagate; there is no rollback of factory side effects.

`Requirement(signature, types={"Money": Money})` requires the exported object to be the very same Python type, tested with `is`. A class with identical fields or the same name fails this constraint. Type checking does not prevent an implementation from exporting the expected type while using some other type internally.

For cross-slot sharing, use `sharing=(("processor.Money", "ledger.Money"),)`. Each path must refer to a declared type export of a dependency. Every pair must resolve to the same live Python type object before factory execution. This captures a narrow explicit nominal sharing obligation; it does not automatically provide family-polymorphic extension or dependent types.

Each composition has a deterministic `identity` computed from the factory declaration, its caller-chosen name, and the dependency modules' logical identities. Every factory invocation still runs, returning a fresh view with a new `instance_id`. There is no memoization of stateful modules. Neither identity proves the factory or dependency code unchanged: names and provider identities are caller-managed. Distinct instances do not automatically generate distinct Python types. A future artifact-backed resolver can use verified content identities for the same fields.

## Metadata and guarantee boundaries

`Signature.metadata()`, `Requirement.metadata()`, `Functor.metadata()`, and `ModuleView.metadata()` return JSON-serializable descriptors. Contract references use `{"id": "payments.processor", "version": "1"}`. Versions match exactly; there is no inferred semantic-version compatibility. Parameter metadata records names, kinds, optionality, and coroutine status without evaluating annotations or formatting arbitrary defaults.

Fixed nominal type obligations serialize only their export names. JSON metadata cannot encode the identity of a live Python class, and these methods do not implement descriptor-to-runtime reconstruction. Discovery can inspect the descriptors separately; real conformance and sharing checks occur on runtime objects.

This system does not claim static ML soundness, proof of behavioral substitution, or enforcement of effects. Sealing is an interface projection, not a sandbox: Python reflection can access internals and arbitrary provider code retains normal process privileges. Module metadata is descriptive evidence, not executable authority or a proof of compatibility.

Run the self-contained example with:

```sh
PYTHONPATH=src python examples/payments.py
```

It substitutes two local fake processors, preserves shared `Money` and `Receipt` objects, and rejects an extra required argument, a different nominal `Money` type, a different contract, and an unfilled module slot. It performs no network requests or financial transactions.

The independent [payment family](../tests/fixtures/payments/family.toml) also exercises the complete repository path:

```sh
PYTHONPATH=src python examples/published_payments.py --work-dir /tmp/payments-repository-demo
```

This builds ordinary wheels from `tests/fixtures/payments/project`, publishes fifteen members through the same generic registry as any other family, discovers two candidates for `payments.processor`, creates exact locks, and imports only the selected generated artifacts. It loads `Money` and `Receipt` independently and checks that both published providers use those identical type objects. A published refund algorithm satisfies another contract within the same family and is rejected as a checkout processor.

The example then atomically imports a published type library and five module factories, linking them through the callback before exposing the resulting bundle. The type library provides shared `Money`, `Receipt`, `PaymentError`, and `TransientPaymentError` identities. Module factories add retry, ledger recording, and request idempotency to an unreliable fake provider. Every parameter is a checked module slot, and the ledger and processor share their types explicitly. The resulting service handles 20 concurrent identical requests with one pre-effect transient failure, one charge effect, and one ledger entry.

This demonstrates behavior added by composition; signature checking alone does not prove that behavior. Exactly one observed effect is scoped to this fake provider's pre-effect transient failures and the same live module instance. A crash or failure after an external effect would need additional transactional or recovery semantics. The work directory retains wheels, the local repository, locks, materialized environments, and `report.json`.

The example also changes composition order while retaining the same published artifacts. With fresh instances, `ledger(idempotency(retry(P)), L)` still produces one charge effect for 20 repeated calls, but records 20 ledger entries: the outer ledger sees each successful return of the cached receipt. In `idempotency(ledger(retry(P), L))`, the outer request cache prevents duplicate calls from reaching the ledger, so it records one entry. Both compositions satisfy the same structural and nominal contracts. This is a concrete failure of commutativity, not a signature incompatibility. The artifact-bundle identity stays equal while the composed binding identity and instance identity differ.
