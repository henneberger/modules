# A checked language above Python modules

The checked composition language supports nominal and associated value types, linear/affine usage, call-scoped borrows, and declared effect bounds. `.mfl` is its source format; TOML supplies published contracts and module requirements. The compiler elaborates checked programs into ordinary Python module constructors and wheels.

The existing TOML graph language remains useful for selecting and connecting whole modules. `.mfl` adds checking of the sequence of operations *inside* a composition. It does not merely count how often an artifact appears in a graph.

## Basis and precise scope

Backpack demonstrates interface-based separate modular development above an existing language, including elaboration to ordinary Haskell modules. That is the architectural precedent for compiling a composition before concrete providers exist. Our implementation has a much smaller type system and does not reproduce Backpack's soundness result. [Backpack, POPL 2014](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf).

Linear Haskell demonstrates a practical coexistence of linear and unrestricted programming. Here, shared values and owned values occupy distinct usage contexts in a restricted composition language. Our call-scoped borrowing and runtime guards are engineering choices, not an implementation of Linear Haskell or Rust's lifetime checker. [Linear Haskell, POPL 2018](https://arxiv.org/abs/1710.09756), [Rust ownership](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html).

The implemented judgment is informally:

```text
Σ ; E ; Γ ; Δ ⊢ program : (T₁, …, Tₙ) ! effects
```

`Σ` contains kinded associated declarations and type constructors. `E` contains explicitly declared equality assumptions. `Γ` contains shared values. `Δ` contains live affine and linear owners. This notation describes the checker, not a published proof of its soundness. Runtime validation and tests support the implementation's stated boundaries.

## Published types and operations

An interface retains its ordinary `callables` and Python `types` export declarations. Optional `typing` adds value-level contracts. These are separate namespaces: an opaque value type need not be an exported Python class.

```toml
[interfaces.typing.types]
Session = { id = "transactions.sqlite.session@1", usage = "linear", representation = "opaque" }
Text = { id = "python.str", usage = "shared", representation = "str" }

[interfaces.typing.operations]
read = { parameters = { session = { type = "Session", mode = "borrow" }, key = { type = "Text", mode = "share" } }, returns = ["Text"], effects = ["sqlite"] }
commit = { parameters = { session = { type = "Session", mode = "move" } }, returns = ["Text"], effects = ["sqlite"] }
```

This is a fragment of a full interface; [interfaces.toml](../families/transactions/interfaces.toml) is executable authoring. Every callable in a typed interface needs a typed operation, and parameter names must match its call shape exactly. This first version accepts synchronous operations with required positional-only or positional-or-keyword parameters. Optional parameters, variadics, keyword-only parameters, and async operations require an explicit adapter or an untyped interface outside this language.

The `id` is a stable nominal identity. Aliases for one identity must agree on usage and representation, including across all interfaces in the checked program. Matching Python representations do not make two distinct identities interchangeable. Canonical literal identities `python.str`, `python.int`, `python.float`, `python.bool`, and `python.bytes` require their matching shared representations.

Representations are `opaque`, `str`, `int`, `float`, `bool`, and `bytes`. Primitive values are runtime-checked against exact builtin types (`bool` is not accepted as `int`). Opaque values are accessible only through declared operations within `.mfl`; ordinary Python adapters can inspect them. These are explicit nominal identities, not fresh generative type identities or existential module types. Two providers claiming the same nominal identity must uphold that compatibility contract.

## Generic value types and explicit assumptions

A value declaration can use a structured `term` instead of a rigid `id`:

```toml
[interfaces.associated.types]
Space = "identity"

[interfaces.associated.constructors."knowledge.VectorBatch"]
parameters = ["identity"]
result = "shared"

[interfaces.typing.types]
Vectors = { term = { apply = "knowledge.VectorBatch", args = [{ var = "Space", kind = "identity" }], kind = "shared" }, usage = "shared", representation = "opaque" }
```

The interface supplies the local associated name `Space`. An encoder operation
returning `Vectors` promises values in the selected encoder's space. An index
accepting its own `Vectors` requires values in its own space. A program connecting
the two must explicitly equate their witnesses:

```toml
[associated]
sharing = [
  [{ from = "embedding.Space", kind = "identity" }, { from = "index.Space", kind = "identity" }]
]
```

These are fragments within full interface/program manifests, not independent
complete programs. Ports must actually declare the referenced names and kinds.
`associated.types` binds any public associated exports to declared terms, such as
`DocumentId = { from = "index.DocumentId", kind = "shared" }`.
`associated.requires` can pin selected port witnesses to concrete nominal terms.
Constructor declarations merge consistently across the sealed interfaces and
manifest.

The checker gives every port its own scope, solves only declared equations, then
checks operation arguments and results under the substitution. A call cannot
invent a missing sharing requirement. Requiring the same interface twice does
not implicitly equate its witnesses. Contradictory rigid identities, missing
exports, unknown constructors, and wrong kinds are rejected.

`typed_associated` records the symbolic assumptions in the certificate and work
contract. Generated initialization checks selected provider metadata, discharges
the equations, and specializes operation descriptors before any body operation
or ownership transfer. A nominal term specializes to that nominal identity;
constructed terms use canonical identities for their complete type expressions.
Modes, representations, and effects retain their meaning after specialization.

This is declaration-based generic checking. Provider Python is trusted to honor
its witness. There are no fresh generative type identities, existential packages,
higher-kinded operations, or inspection of arbitrary Python bodies. See the
[associated-type guide](associated-types.md#generic-operation-bodies) for the
complete workflow and runnable SQLite knowledge-base example.

## Source grammar

A manifest declares the public interface and required module ports:

```toml
[module]
id = "transaction"
version = "1.0.0"
provides = { id = "transactions.workflow", version = "1" }

[ports.store]
requires = { id = "transactions.store", version = "1" }

[program]
source = "transaction.mfl"
export = "run"

[policy]
allowed_effects = ["sqlite"]
```

The full [example manifest](../examples/typed/transaction.toml) also has `schema_version`, family, and publisher fields. Source paths must be relative `.mfl` paths contained within the manifest directory. Program authoring is file-based TOML, not arbitrary JSON dictionaries containing executable source.

The public typed operation supplies input variable names and result types. A program implements one exported operation. Accepted statements are:

| Form | Meaning |
| --- | --- |
| `x = port.operation(args)` | Call a declared operation and bind one result. |
| `x, y = port.operation(args)` | Bind an exact-arity result tuple. |
| `port.operation(args)` | Call an operation with no results. |
| `x = value` | Copy a shared value or move an owner into a fresh binding. |
| `discard(x)` | Explicitly drop an affine owner. |
| `return x, y` | Return shared values or transfer owners outward. |
| Terminal `if flag: … else: …` | Check each Boolean branch as a complete return path. |

Values are names or primitive literals. Arguments may use positional or explicit keyword syntax, checked against the published call shape. Bindings use fresh names; rebinding is not permitted. Return computations must first be bound to names. Each branch must be terminal and have an else path, avoiding implicit joins or resource-state merging. A zero-result program may return implicitly after discharging its linear obligations.

Imports, loops, comprehensions, functions/classes, reflection, arbitrary attribute access, subscripting, arithmetic, arbitrary calls, `try`, `with`, and async syntax are rejected. Such behavior belongs in separately declared Python operations. The source is parsed using Python's AST parser, but only accepted nodes are elaborated. The compiler never executes `.mfl` as Python. Source is limited to 1 MB and 10,000 syntax nodes.

## Resource rules

**Shared** values permit contraction and weakening: reuse and omission. **Affine** values forbid duplicate ownership use but permit omission. **Linear** values must leave the live context exactly once on each normal return path.

An operation parameter with `move` removes the owned binding from the live context. Later use fails. Assigning an owned value to a new variable also moves it; renaming cannot create an alias usable by checked code. Returning an owner transfers it to the caller. Returning the same owner twice fails.

`borrow` permits temporary operation access without consuming the owner. The borrowed call must finish before subsequent checked statements execute. Duplicate borrows within a single call are permitted; a borrow and move of the same owner in one call are rejected. Borrowing is not a read-only promise: the operation's behavioral/effect contract determines allowed mutation. There are no inferred lifetimes, borrow-return types, or concurrent-task syntax.

The public entry operation currently accepts `share` and `move` inputs. Borrowing is supported at calls inside a program, not as a borrowed public entry parameter. Entry guards preflight all inputs and transfer incoming ownership before executing the checked body. Consequently, ignoring an affine parameter still invalidates the caller's old handle.

A linear obligation is about ownership transfer. It is not a proof that an external database commit happened exactly once. A consumer may transfer ownership onward, and an operation may diverge or raise. Our static guarantee applies to normal return paths. Exception cleanup and recovery remain part of trusted adapter contracts; `discard` does not imply closing a resource.

## Effects and protocols

The checker unions the declared effects of all potentially called operations, including both branches. That set must fit the public operation's effect bound and the manifest's optional `allowed_effects`. Merely selecting a module with a `read` label is insufficient: each operation has its own declared effect set.

Protocols can use different nominal types for different states. For example, an operation can consume `OpenTransaction` and return `PreparedTransaction`; `commit` can then require the latter. The checker uses ordinary nominal compatibility and move rules to enforce that sequence. There is no independent automaton DSL or inferred state machine. The included SQLite candidate demonstrates open-session consumption through commit/abort, not a distributed transaction protocol.

Numerical budgets require additional arithmetic constraints or runtime accounting. Making a budget token linear prevents copying it in checked code; it does not prove that child allocations sum to the original allowance. That stronger solver is not implemented.

## Separate compilation and linking

`check-program` queries exact interface records only. `build-program` does the same checking and emits an open constructor. Neither operation discovers, downloads, imports, or requires concrete provider implementations. Tests explicitly forbid provider lookup during checking/building.

```bash
.venv/bin/python examples/typed_system.py --work-dir .mf/typed-example
.venv/bin/mf check-program examples/typed/transaction.toml \
  --registry .mf/typed-example/repository --out .mf/type-report.json
.venv/bin/mf build-program examples/typed/transaction.toml \
  --registry .mf/typed-example/repository --out .mf/typed-build
.venv/bin/mf publish .mf/typed-build/index.json \
  --registry .mf/typed-example/repository
```

The first command performs the entire prepared example; use an empty work directory. Optional `--wheelhouse .mf/upstream-wheels` makes environment resolution offline too when that directory contains a suitable packaging wheel. The resulting program environment always replays without repository access.

A compiled program is an ordinary `functor` member with required module ports. The existing synthesizer finds providers for those ports, and existing assembly/environment locks pin the result. `build-module` can include a checked program as a node. Typed graph projections preserve resolved parameter types/modes and result types, and cannot widen the source operation's effects beyond the target contract. Projecting to an untyped interface explicitly leaves this typed guarantee.

Typing makes an authored composition admissible or rejects it. The synthesizer still searches published constructors and their requirements; it does not generate arbitrary `.mfl` source or prove capability labels. Agents can propose source, run the checker, and revise rejected candidates.

## Generated artifacts and Python trust

The build emits normal wheels plus `generated.py`, `certificate.json`, and `work-contract.json`. The certificate records source hash, exact interface specifications, associated assumptions and substitutions, typed IR, inferred effects, and the remaining trusted-operation boundary. Its hash is stored in published member metadata. It is an inspectable checker report; it is neither a proof assistant certificate nor a signature authenticating its author.

Generated code calls small ownership guards and then selected Python functions. It performs no provider search or source parsing at runtime. `Owned` handles track identity, usage, validity, and active borrows; copies and serialization are rejected. Moves invalidate before adapter execution. Multi-argument preflight completes before any input is consumed. Borrow locks cover the synchronous call and release on failure. Nested checked operations preserve their handles rather than unwrapping them as raw adapters. This also holds for a generic owned result such as `Lease[store.Domain]`: inner and outer constructors specialize their descriptors to the same concrete identity, so a returned handle can be passed onward under the outer move/borrow rules. A declaration mismatch is rejected before either body executes.

Return validation checks primitive representations, nominal handle identities for nested checked code, and arity. It rejects direct and ordinary-container escape of borrowed values and duplicate owned return identities within a call. This is not an alias analysis of arbitrary Python heaps.

Trusted Python adapters receive raw implementation values. They can retain aliases, return the same allocation in different calls, mutate globals, fabricate declarations, hide effects, or violate termination/cleanup assumptions. The system does not prevent those behaviors or make Python a sandbox. Raw adapter contracts must be reviewed/tested, or implemented behind a stronger isolation boundary where appropriate. `implementation_trust = "checked-composition"` describes checked orchestration; it does not certify every dependency's implementation.

The [SQLite adapter](../adaptations/transactions/README.md) is a concrete small boundary: it creates real sessions, supports borrowing operations, and closes them on commit/abort. It is intentionally ordinary Python. The [rejected examples](../examples/typed/rejected-use-after-move.mfl) demonstrate violations caught in composition without inspecting that implementation.
