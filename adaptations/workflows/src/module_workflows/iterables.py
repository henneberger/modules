"""Representation and composition adapters; algorithms come from dependencies."""


def deduplicated_batches(*, chunker, deduplicator):
    """Construct a finite-batch interface from two independently supplied modules."""

    def run(items, size):
        if isinstance(items, (str, bytes)):
            raise TypeError("batch pipeline accepts records, not strings or bytes")
        if type(size) is not int or size < 1:
            raise ValueError("batch size must be a strictly positive integer")
        return chunker.chunk(deduplicator.unique(items), size)

    return {"run": run}


def batches(*, chunker):
    """Expose the same pipeline interface without a deduplication stage."""

    def run(items, size):
        if isinstance(items, (str, bytes)):
            raise TypeError("batch pipeline accepts records, not strings or bytes")
        if type(size) is not int or size < 1:
            raise ValueError("batch size must be a strictly positive integer")
        return chunker.chunk(items, size)

    return {"run": run}
