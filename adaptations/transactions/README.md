# A trusted Python transaction adapter

This small adapter makes the typed module boundary concrete using Python's
standard-library `sqlite3`. The family contract lives in
[interfaces.toml](../../families/transactions/interfaces.toml); the independently
published implementation is `sqlite.store`, declared in
[providers.toml](../../families/transactions/providers.toml).

`create()` returns ordinary Python operations. `begin()` opens a new in-memory
database and returns an opaque `Session`. A program can borrow the session for
`put(session, key, value)` and `read(session, key)`, then consume it with exactly
one of `commit(session)` or `abort(session)`. `commit` returns the string
`"committed"`; `abort` returns nothing. Both close the connection. Each session
owns an independent database, so committed data is not retained after closure.
The example demonstrates transaction resource usage, not durable storage.

The module language checks the resource protocol at composition time:

| Operation | Session usage | Result |
| --- | --- | --- |
| `begin` | Creates a linear resource | Opaque session |
| `put` | Borrows; ownership stays with the caller | None |
| `read` | Borrows; ownership stays with the caller | Shared string |
| `commit` | Moves; the caller loses ownership | Shared receipt string |
| `abort` | Moves; the caller loses ownership | None |

The implementation has no ownership-wrapper dependency and uses no special
Python syntax. For illustration, the underlying Python operations perform:

```python
store = create()
session = store["begin"]()
try:
    store["put"](session, "question", "What can this module solve?")
    answer = store["read"](session, "question")
except Exception:
    store["abort"](session)
    raise
else:
    receipt = store["commit"](session)
```

This Python illustration does not itself receive static ownership checking.
The typed composition language provides that checking for its own programs.
The adapter is explicitly marked `implementation_trust = "trusted-python"`:
the compiler trusts that these Python operations obey the published contract.
Unrestricted Python could retain aliases or inspect the session's internals;
declaring an opaque type does not prove that arbitrary Python respects it.

Missing keys raise `KeyError`. A failed read or write leaves the session open
for the caller to abort; commit and abort close even when the underlying
database operation raises. Calls on consumed sessions raise `RuntimeError`.
The straight-line typed program's normal-return ownership check is not an
exception cleanup proof. SQLite sessions are local to their creating thread;
this example provides no distributed transaction or exactly-once network
effect guarantee.
