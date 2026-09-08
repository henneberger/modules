# Associated types in module graphs

Version 0.6.0 adds kinded type terms, abstract associated declarations, and substitution through the existing module graph builder. An open module can preserve a relationship between its dependencies without choosing their implementations or assigning one global concrete name to every type it uses.

The same declarations are checked by repository assembly resolution, constructor synthesis, lock verification, and generated graph preflight. There is no new resolver command: use `resolve-module`, `build-module`, and `synthesize` as before.

## A type belongs to a module

An embedding signature can declare an associated `Space`. A concrete provider supplies the witness for that space. A cache constructor can export `base.Space`, preserving the input's identity without knowing which encoder will eventually be selected.

```toml
# Fragment inside a published interface.
[interfaces.associated.types]
Space = "identity"

[interfaces.associated.constructors."knowledge.VectorBatch"]
parameters = ["identity"]
result = "shared"
```

The type constructor is a first-order declaration: `knowledge.VectorBatch` accepts one identity argument and produces a shared value type. Constructor identifiers have fixed declarations; conflicting arities or kinds fail when their registries meet. Constructors are declarations, not executable Python or arbitrary type-level functions.

A value-level contract can connect an operation's result to the module's space:

```toml
[interfaces.typing.types]
Texts = { id = "knowledge.TextBatch", usage = "shared", representation = "opaque" }
Vectors = { term = { apply = "knowledge.VectorBatch", args = [{ var = "Space", kind = "identity" }], kind = "shared" }, usage = "shared", representation = "opaque" }

[interfaces.typing.operations]
embed = { parameters = { texts = { type = "Texts", mode = "share" } }, returns = ["Vectors"], effects = ["memory"] }
```

These are fragments of an interface that also declares `id`, `version`, and the ordinary `embed(texts)` call shape. Every local `var` must refer to an associated declaration in that interface. Its kind must agree. The graph linker substitutes the chosen module witness into the operation's value types before checking typed export compatibility.

A provider cannot make `VectorBatch[SpaceA]` equal `VectorBatch[SpaceB]` by returning arrays with equal dimensions. Nor can a citation operation use one document-ID domain where another is required merely because both use Python strings.

## The term language

Terms are structured TOML values with one of four forms:

| Form | Meaning | Where it is permitted |
| --- | --- | --- |
| `{ nominal = "encoder:v1", kind = "identity" }` | A rigid declared identity. | Interfaces, members, graphs. |
| `{ var = "Space", kind = "identity" }` | A local associated type of the current interface. | Interface value-type declarations. |
| `{ from = "base.Space", kind = "identity" }` | A witness projected from a dependency module. | Member and graph witness declarations. |
| `{ apply = "knowledge.VectorBatch", args = [TERM], kind = "shared" }` | Apply a registered type constructor. | Interfaces, members, graphs. |

Kinds are `identity`, `shared`, `affine`, and `linear`. An identity is a type-level domain marker, not itself a value that can be passed to an operation. A term used as a value type must have the kind corresponding to its declared usage. Existing `id` value declarations remain rigid nominal value types; a value declaration has exactly one of `id` or `term`.

`from` paths are exactly `slot.Name` in member metadata and `node.Name` or `port.Name` in graph authoring. Interface-local variables cannot reference another module directly. Nested constructor applications are allowed; terms are bounded to depth 32 and 1,000 nodes. Unknown constructors, wrong arities, wrong kinds, unresolved projections, cycles during substitution, and occurs-check violations are rejected.

## Supply, propagate, and constrain witnesses

A concrete provider supplies its declared witness:

```toml
# Fragment inside a family member.
[members.associated.types]
Space = { nominal = "sklearn:hashing:1024:unsigned:l2", kind = "identity" }
```

A constructor preserves its dependency's witness:

```toml
# This member must separately declare requires.base = EMBEDDING.
[members.associated.types]
Space = { from = "base.Space", kind = "identity" }
```

A retrieval constructor might expose both its encoder's space and its store's document domain:

```toml
[members.associated.types]
Space = { from = "embedding.Space", kind = "identity" }
DocumentId = { from = "data.DocumentId", kind = "shared" }
```

A harness using a retriever and a store can require agreement between their document domains:

```toml
[members.associated]
sharing = [
  [{ from = "retrieval.DocumentId", kind = "shared" }, { from = "data.DocumentId", kind = "shared" }]
]
```

A requirement can also pin an associated witness to a particular domain:

```toml
[members.associated.requires.embedding]
Space = { nominal = "sklearn:hashing:1024:unsigned:l2", kind = "identity" }
```

These fragments require the corresponding named dependency slots. Every associated export must match the provided interface's declared export name and kind. Member terms using constructors must include the relevant registry under `associated.constructors`; constructor registries merge consistently through composition.

## Open graphs retain their assumptions

A graph exports associated witnesses separately from its Python operations:

```toml
[associated.types]
Space = { from = "embedding.Space", kind = "identity" }
DocumentId = { from = "data.DocumentId", kind = "shared" }

[constraints]
same_associated = [["query.Space", "index.Space"]]
```

The paths above must name actual nodes or public ports in the complete graph. Explicit graph constraints and member requirements become equations. Typed export projections also contribute equations after parameter modes, representations, and effect bounds have been checked.

A concrete provider's nominal witnesses are rigid. Unfilled public ports introduce scoped variables. The solver can constrain those variables, but it cannot merge two different rigid identities. Constructor applications compare by constructor identity and recursively by their arguments.

For example, an open graph can establish `query.Space = index.Space` while neither provider exists. Its published member retains that obligation in `associated.sharing`. Closing it with equal witnesses succeeds; closing it with different witnesses fails. Nested publication substitutes the enclosing graph's actual links into those obligations. Two uses of the same open constructor get the scopes of their own supplied ports, rather than accidentally sharing an inference variable.

The emitted residual equations are essential. Rewriting both sides of an equality to the same solved variable and discarding the equation would make the published constructor forget what its consumer must provide. The implementation retains the substitution's binding obligations, and tests exercise this across publication and nested closure.

Generated `work-contract.json` includes the public/port interfaces and the resulting associated witness declarations and residual equations. The compiled wheel retains the graph and its type assumptions. A downstream contributor need not inspect the original source to preserve those assumptions.

## Types and instances remain different

`same_associated` establishes agreement between declared type domains. `same_instance` establishes that two slots receive the same graph node. `same_type` concerns exported Python class objects. Existing semantic `same_index` constraints remain available.

Two SQLite connections can share a document-ID type while storing different data. Associated types therefore do not replace instance sharing or behavioral evaluation. Conversely, two consumers sharing one connection may still mishandle document identities in arbitrary Python. The [contribution workflow](contributions.md) supplies a separate place for evaluation evidence.

## Runtime and synthesis

Planning resolves concrete witness expressions and checks dependency equalities without importing implementations. Synthesis rejects candidates whose associated exports do not match the published interface. Assembly locking and verification repeat the checks against the sealed interface records.

Generated graph preflight checks supplied port metadata and resolves all selected witness relationships before invoking its factories. Runtime module views carry defensive copies of the closed witness metadata and constructor registry. Associated metadata survives constructor composition and final assembly sealing.

These are checks of declarations. Provider Python remains trusted to honor its value types and semantic identities. Generated initialization can still import Python with side effects, and a failed link is not a rollback of arbitrary effects.

## Run the real example

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python examples/associated_system.py --work-dir .mf/associated
```

Use an empty work directory. The example reuses Mari MMR, scikit-learn lexical vectors, caching, SQLite, and Python's executor. It authors associated variants of the existing interfaces and graphs, publishes the open retrieval module, closes and publishes the system, and executes it in a locked environment. It also checks contradictory vector-space metadata and a mismatched document-ID domain. See [example provenance](../adaptations/associated/README.md).

Dependency locking can download the required wheels. For offline resolution, pass `--wheelhouse PATH` with the wheelhouse described in the [knowledge guide](module-build.md#run-the-real-candidate-system).

## Scope of this increment

Implemented: kinded first-order terms, interface-local associated variables, explicit provider witnesses, constructor applications, scoped substitution, residual graph equalities, typed export projection checks, publication, synthesis, and runtime metadata validation.

Not yet implemented: associated-value substitution within `.mfl` operation bodies, generated fresh type witnesses, applicative identity inference from a constructor's artifact and arguments, existential packaging, higher-kinded types, recursive modules, subtyping, or a soundness proof. `.mfl` rejects interfaces using associated value terms with an explicit diagnostic; it does not silently discard those types. Graph-level type substitution preserves ownership declarations, but Python operation bodies remain outside the checker.

The broader [module calculus design](module-calculus.md) describes those later steps. This release implements the graph-linking portion rather than presenting the full proposed calculus as executable.
