from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int
    currency: str = "USD"


@dataclass(frozen=True)
class Receipt:
    processor: str
    amount: Money
    idempotency_key: str


def local_processor(name: str):
    receipts = {}

    def charge(amount: Money, *, idempotency_key: str) -> Receipt:
        if not isinstance(amount, Money) or amount.cents <= 0:
            raise ValueError("amount must be positive Money")
        if not idempotency_key:
            raise ValueError("idempotency key is required")
        if idempotency_key in receipts and receipts[idempotency_key].amount != amount:
            raise ValueError("idempotency key already used for a different amount")
        return receipts.setdefault(
            idempotency_key, Receipt(name, amount, idempotency_key)
        )

    return {"charge": charge, "Money": Money, "Receipt": Receipt}
