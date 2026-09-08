"""Shared nominal records for local processor and refund contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    """An integer amount in minor units and its currency code."""

    cents: int
    currency: str = "USD"


@dataclass(frozen=True)
class Receipt:
    """A simulated charge identified within one processor instance."""

    processor: str
    amount: Money
    idempotency_key: str


@dataclass(frozen=True)
class Refund:
    """A local refund calculation; it performs no payment operation."""

    original: Receipt
    amount: Money


class PaymentError(Exception):
    """A local processor or composition rejected a payment request."""


class TransientPaymentError(PaymentError):
    """This fake provider failed before performing its charge effect."""


def types_library() -> dict:
    """Export one coherent reusable library of payment types and exceptions."""
    return {
        "Money": Money,
        "Receipt": Receipt,
        "PaymentError": PaymentError,
        "TransientPaymentError": TransientPaymentError,
    }


def validate_charge(amount: Money, idempotency_key: str) -> None:
    """Check the values accepted by both local payment processor implementations."""
    if (
        not isinstance(amount, Money)
        or not isinstance(amount.cents, int)
        or amount.cents <= 0
    ):
        raise ValueError("amount must be Money with positive integer cents")
    if amount.currency not in {"USD", "EUR"}:
        raise ValueError("local processors support USD and EUR")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("idempotency_key must be a nonempty string")
