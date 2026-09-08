"""Beta: a local log-backed processor implementing the same declared interface."""

from .types import Money, Receipt, validate_charge


def beta_processor() -> dict:
    """Create a fresh fake processor with a receipt log and linear request lookup."""
    history: list[Receipt] = []

    def charge(amount: Money, *, idempotency_key: str) -> Receipt:
        validate_charge(amount, idempotency_key)
        for receipt in history:
            if receipt.idempotency_key == idempotency_key:
                if receipt.amount != amount:
                    raise ValueError(
                        "idempotency key was already used for a different amount"
                    )
                return receipt
        receipt = Receipt("beta", amount, idempotency_key)
        history.append(receipt)
        return receipt

    return {"charge": charge, "Money": Money, "Receipt": Receipt}
