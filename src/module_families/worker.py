"""Private process entry point for an installed, locked assembly environment."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import sys

from .assemblies import instantiate
from .cli import _json_default
from .environments import FrozenRepository, verify_environment


def _result(value):
    """Resolve asynchronous exports and bound streamed CLI results."""

    async def resolve(awaitable):
        return await awaitable

    async def collect(iterator):
        items = []
        async for item in iterator:
            items.append(item)
            if len(items) > 10000:
                raise ValueError(
                    "result exceeds 10,000 items; consume through the Python API"
                )
        return items

    if inspect.isawaitable(value):
        value = asyncio.run(resolve(value))
    if hasattr(value, "__aiter__"):
        return asyncio.run(collect(value))
    if hasattr(value, "__next__"):
        from itertools import islice

        value = list(islice(value, 10001))
        if len(value) > 10000:
            raise ValueError(
                "result exceeds 10,000 items; consume through the Python API"
            )
    return value


def main():
    path, target, export, args, kwargs = sys.argv[1:]
    lock, wheelhouse = verify_environment(path)
    repository = FrozenRepository(lock, wheelhouse)
    with contextlib.redirect_stdout(sys.stderr):
        instance = instantiate(lock["assembly"], repository, target)
        function = instance.module[export]
        result = _result(function(*json.loads(args), **json.loads(kwargs)))
    print(
        json.dumps(
            {"assembly": instance.identity, "export": export, "result": result},
            default=_json_default,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
