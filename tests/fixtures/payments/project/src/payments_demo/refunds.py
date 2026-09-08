"""A distinct refund-calculation contract within the payment domain family."""

from .types import Receipt, Refund


def full_refund(receipt: Receipt) -> Refund:
    """Calculate a complete simulated refund from a receipt; no external effects."""
    if not isinstance(receipt, Receipt):
        raise TypeError("receipt must use this family's Receipt type")
    return Refund(receipt, receipt.amount)
