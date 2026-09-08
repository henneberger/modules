"""Local payment providers: substitution, explicit contracts, and nominal sharing.

Run: PYTHONPATH=src python examples/payments.py
No real payment, network access, or external service is involved.
"""

from dataclasses import dataclass

from module_families.contracts import ContractError, Functor, Requirement, Signature


@dataclass(frozen=True)
class Money:
    cents: int
    currency: str = "USD"


@dataclass(frozen=True)
class Receipt:
    processor: str
    amount: Money
    idempotency_key: str


def charge_interface(amount: Money, *, idempotency_key: str) -> Receipt:
    """Return a receipt; the key identifies repeated requests within this provider."""
    raise NotImplementedError("signature prototype")


PROCESSOR = Signature(
    "payments.processor", "1", {"charge": charge_interface}, ("Money", "Receipt")
)
CHECKOUT = Signature(
    "payments.checkout", "1", {"charge": charge_interface}, ("Money", "Receipt")
)


def local_processor(name: str):
    receipts = {}

    def charge(amount: Money, *, idempotency_key: str) -> Receipt:
        if not isinstance(amount, Money) or amount.cents <= 0:
            raise ValueError("amount must be positive Money")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if idempotency_key in receipts and receipts[idempotency_key].amount != amount:
            raise ValueError("idempotency key already used for a different amount")
        return receipts.setdefault(
            idempotency_key, Receipt(name, amount, idempotency_key)
        )

    return PROCESSOR.seal(
        {
            "charge": charge,
            "Money": Money,
            "Receipt": Receipt,
            "private_receipts": receipts,
        },
        provides={"id": "payments.processor", "version": "1"},
        identity=f"local.{name}@1",
    )


def checkout_factory(*, processor):
    return {
        "charge": processor.charge,
        "Money": processor.Money,
        "Receipt": processor.Receipt,
    }


CHECKOUT_FACTORY = Functor(
    "payments.checkout@1",
    {"processor": Requirement(PROCESSOR, types={"Money": Money, "Receipt": Receipt})},
    CHECKOUT,
    checkout_factory,
)


def expect_rejection(label, action):
    try:
        action()
    except ContractError as exc:
        print(f"Rejected {label}: {exc}")
    else:
        raise AssertionError(f"expected rejection of {label}")


def main():
    for provider in (local_processor("alpha"), local_processor("beta")):
        checkout = CHECKOUT_FACTORY(processor=provider)
        receipt = checkout.charge(Money(1250), idempotency_key="order-42")
        assert checkout.charge(Money(1250), idempotency_key="order-42") is receipt
        print(
            f"{receipt.processor}: {receipt.amount.currency} {receipt.amount.cents / 100:.2f}"
        )

    def wrong_charge(amount, account, *, idempotency_key):
        return None

    expect_rejection(
        "extra required argument",
        lambda: PROCESSOR.seal(
            {"charge": wrong_charge, "Money": Money, "Receipt": Receipt}
        ),
    )

    @dataclass(frozen=True)
    class OtherMoney:
        cents: int
        currency: str = "USD"

    exports = dict(local_processor("gamma"))
    exports["Money"] = OtherMoney
    incompatible = PROCESSOR.seal(exports, identity="local.gamma@1")
    expect_rejection(
        "different Money identity", lambda: CHECKOUT_FACTORY(processor=incompatible)
    )
    expect_rejection(
        "different contract",
        lambda: PROCESSOR.seal(
            exports, provides={"id": "payments.refund", "version": "1"}
        ),
    )
    expect_rejection("missing processor", lambda: CHECKOUT_FACTORY())


if __name__ == "__main__":
    main()
