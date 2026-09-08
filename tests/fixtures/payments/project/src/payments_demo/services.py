"""A deliberately unreliable fake processor, with observable local effects."""

from .types import (
    Money,
    PaymentError,
    Receipt,
    TransientPaymentError,
    types_library,
    validate_charge,
)


def flaky_processor() -> dict:
    """Fail once per key before any effect, then charge on every successful call.

    This provider deliberately has no idempotency: repeated successful requests
    produce additional effects unless a composing module supplies that behavior.
    """
    attempted: set[str] = set()
    attempts = []
    effects = []

    def charge(amount: Money, *, idempotency_key: str) -> Receipt:
        try:
            validate_charge(amount, idempotency_key)
        except ValueError as exc:
            raise PaymentError(str(exc)) from exc
        attempts.append(idempotency_key)
        if idempotency_key not in attempted:
            attempted.add(idempotency_key)
            raise TransientPaymentError(
                "simulated interruption before the charge effect"
            )
        receipt = Receipt("flaky-local", amount, idempotency_key)
        effects.append(receipt)
        return receipt

    def effect_count() -> int:
        return len(effects)

    def attempt_count() -> int:
        return len(attempts)

    return {
        **types_library(),
        "charge": charge,
        "effect_count": effect_count,
        "attempt_count": attempt_count,
    }
