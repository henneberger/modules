# Payments demonstration family

This independent Python project exercises the repository system with a domain unrelated to Mari. It contains two local processor implementations, a reusable nominal type library, a refund calculation, and modules that produce richer modules. It has no dependency on Mari, the module-system runtime, an external payment processor, or a network service.

`alpha_processor()` uses a dictionary for request lookup; `beta_processor()` uses a receipt log. Both return module exports containing `charge(amount, *, idempotency_key)`, `Money`, and `Receipt`. Each factory creates independent process-local state. Reusing a key for the same amount returns the existing receipt; using it for another amount fails. These are simulated operations, and their in-memory state does not persist across processes.

The family declaration is [the fixture family TOML](../family.toml). The generic compiler publishes the processor factories, refund algorithm, and records as separate members with shared content cells. The published example independently locks and loads them, then uses runtime contracts to compose the providers. Contract metadata describes the module produced by a factory; it does not claim that the zero-argument factory itself has the `charge` signature.

The separately published `types.library` exports `Money`, `Receipt`, `PaymentError`, and `TransientPaymentError` as one coherent reusable library. The example imports it with five other module factories in an atomic bundle, then links:

```text
idempotency(ledger(retry(flaky-processor), memory-ledger))
```

Named module parameters and nominal sharing constraints govern every composition. `flaky-processor` fails once per key before effects and has no idempotency. Retry adds a bounded retry policy; ledger adds recording after success; outer idempotency serializes duplicate callers and remembers successful receipts. The resulting service handles 20 concurrent calls with the same key using two attempts, one charge effect, and one ledger entry. These properties are checked for this fake provider and live process state. A provider that performs its effect and then fails would require a stronger recovery protocol. Restart durability is outside this example.

Changing the order to `ledger(idempotency(retry(P)), L)` produces one charge effect and 20 ledger entries for the same 20-call workload, using fresh instances of the same published modules. An outer ledger records every successful return, including a receipt returned from the idempotency cache. Both orders satisfy the same checked interfaces; their observable behavior differs. The example records this comparison in `report.json`, separating the shared artifact graph from the different composed binding identities.

From the repository root:

```sh
PYTHONPATH=src python examples/published_payments.py
```
