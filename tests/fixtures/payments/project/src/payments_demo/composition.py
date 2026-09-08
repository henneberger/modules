"""Module-producing modules that add retry, journaling, and request idempotency."""

from threading import RLock

from .types import Receipt, types_library


def memory_ledger() -> dict:
    """Create a local append-only receipt ledger; no external storage is used."""
    recorded = []
    lock = RLock()

    def record(receipt: Receipt) -> None:
        with lock:
            recorded.append(receipt)

    def entries() -> tuple:
        with lock:
            return tuple(recorded)

    return {**types_library(), "record": record, "entries": entries}


def retry_processor(*, processor) -> dict:
    """Retry this contract's pre-effect transient failures, at most three attempts."""
    TransientPaymentError = processor["TransientPaymentError"]

    def charge(amount, *, idempotency_key):
        for attempt in range(3):
            try:
                return processor["charge"](amount, idempotency_key=idempotency_key)
            except TransientPaymentError:
                if attempt == 2:
                    raise

    return {**dict(processor), "charge": charge}


def ledger_processor(*, processor, ledger) -> dict:
    """Record a receipt only after a downstream charge returns successfully."""

    def charge(amount, *, idempotency_key):
        receipt = processor["charge"](amount, idempotency_key=idempotency_key)
        ledger["record"](receipt)
        return receipt

    return {**dict(processor), "charge": charge}


def idempotent_processor(*, processor) -> dict:
    """Memoize successful receipts by request key within this live module instance.

    A lock covers the downstream call and cache insertion, including concurrent
    duplicate callers. A failure leaves no cached success. This does not recover
    from a provider performing its effect and then failing before returning.
    """
    receipts = {}
    lock = RLock()
    PaymentError = processor["PaymentError"]

    def charge(amount, *, idempotency_key):
        with lock:
            if idempotency_key in receipts:
                receipt = receipts[idempotency_key]
                if receipt.amount != amount:
                    raise PaymentError(
                        "idempotency key was already used for a different amount"
                    )
                return receipt
            receipt = processor["charge"](amount, idempotency_key=idempotency_key)
            receipts[idempotency_key] = receipt
            return receipt

    return {**dict(processor), "charge": charge}
