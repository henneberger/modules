"""Alpha: a local dictionary-backed processor with explicit request deduplication."""

from .types import Money, Receipt, validate_charge


def alpha_processor() -> dict:
    """Create a fresh fake processor; repeated identical requests share a receipt."""
    receipts: dict[str, Receipt] = {}

    def charge(amount: Money, *, idempotency_key: str) -> Receipt:
        validate_charge(amount, idempotency_key)
        if idempotency_key in receipts:
            receipt = receipts[idempotency_key]
            if receipt.amount != amount:
                raise ValueError(
                    "idempotency key was already used for a different amount"
                )
            return receipt
        receipt = Receipt("alpha", amount, idempotency_key)
        receipts[idempotency_key] = receipt
        return receipt

    return {"charge": charge, "Money": Money, "Receipt": Receipt}
