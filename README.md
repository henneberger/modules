# Module Families

A Python module build system and repository that **synthesizes executable programs from interface and capability goals**. It adapts existing Python into independently published components, discovers implementations, recursively fills module requirements, and locks a selected composition for execution.

Families are shared ecosystems. A publisher owns its contributions, rather than every implementation in a family. Interfaces, reusable type libraries, implementations, and module constructors can be released separately. Packaging and graph authoring use TOML; checked compositions use `.mfl`; expanded indexes, search results, and locks are generated JSON. There are no compatibility paths for earlier manifests or repository schemas.

Version 0.4.0 adds a **checked module composition language**, alongside the TOML graph builder. TOML publishes interfaces and connects module ports; `.mfl` expresses programs with checked value types, resource usage, and declared effects; ordinary Python implements the operations. The compiler produces normal Python wheels. Agents can check and publish a composition against interfaces before its concrete providers exist.

## Why extend the language?

A module signature should describe more than which functions exist. A transaction handle can be borrowed for an operation, transferred into commit, and then become unavailable. An embedding vector can belong to a specific nominal type. A composition can require that every called operation fits an effect bound. Those are rules for constructing programs, and a checked language can reject violations before running Python.

Our `.mfl` language deliberately accepts a small Python-shaped grammar. It is compiled separately; a `.mfl` file is not an unrestricted Python script. This follows Backpack's useful separation between checking against interfaces and later choosing implementations. It is not a complete implementation of Backpack's type system.

| Layer | What users write | What it establishes |
| --- | --- | --- |
| Contracts and packaging | TOML | Exact interfaces, nominal type identities, usage modes, effect declarations, and repository requirements. |
| Checked composition | `.mfl` | Type compatibility, resource accounting on every normal return path, and a bound on declared operation effects. |
| Implementations | Ordinary Python | Actual algorithms, model calls, stores, and resource operations; their behavioral contracts remain trusted. |
| Distribution | Generated wheels and locks | Exact compiled code and selected dependency closure for replay. |

## Learn through a transaction

The [published store contract](families/transactions/interfaces.toml) declares an opaque **linear** `Session` and reusable `Text` values. Its operations have these meanings:

```text
begin()                              -> Session
put(borrow Session, share Text, share Text) -> ()
read(borrow Session, share Text)      -> Text
commit(move Session)                 -> Text
abort(move Session)                  -> ()
```

`borrow` lends the handle for one synchronous call. `move` transfers ownership and makes the old binding unavailable. Borrowing does not itself imply read-only access: `put` mutates the session under its declared effect contract.

The actual [transaction.mfl](examples/typed/transaction.mfl) composition is:

```python
# .mfl — checked composition; key and value come from the public contract.
session = store.begin()
store.put(session, key, value)
observed = store.read(session, key)
receipt = store.commit(session)
return observed, receipt
```

The compiler knows that the two middle calls borrow `session`, and that commit consumes it. Adding `store.read(session, key)` after commit produces **use after move**. Removing commit while returning `observed` produces **unconsumed linear resource**. These are build errors, even when no store implementation has been published.

```toml
# Excerpt from the interface's typing declaration
[interfaces.typing.types]
Session = { id = "transactions.sqlite.session@1", usage = "linear", representation = "opaque" }
Text = { id = "python.str", usage = "shared", representation = "str" }

[interfaces.typing.operations]
commit = { parameters = { session = { type = "Session", mode = "move" } }, returns = ["Text"], effects = ["sqlite"] }
```

Local type names are aliases; the `id` defines nominal equality. `opaque` keeps representation outside the checked language. It does not prevent reflection by arbitrary Python. Different nominal identities remain incompatible even if both implementations use dictionaries internally.

The three usage rules are:

- **Shared:** values may be reused or ignored.
- **Affine:** ownership may be transferred at most once; ignoring it or calling `discard(value)` is permitted.
- **Linear:** ownership must be transferred exactly once on every normally returning path, either into another operation or out through the return value. Temporary borrows do not consume ownership.

Branches are checked independently. This is accepted when `keep` is a declared Boolean:

```python
# .mfl fragment
session = store.begin()
if keep:
    receipt = store.commit(session)
    return receipt
else:
    store.abort(session)
    return "aborted"
```

Both paths discharge the same resource obligation. A branch that simply forgets the session is rejected. Effects are inferred from called operation contracts and checked against the result contract and optional policy. A `sqlite` effect cannot fit an empty effect bound. This does not inspect Python bodies for hidden effects.

## Run the separately checked example

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python examples/typed_system.py --work-dir .mf/typed-example
```

Use an empty directory. The script publishes interfaces, checks and builds the program **before any store provider exists**, verifies two rejected programs, then publishes a real SQLite adapter. Synthesis fills the open `store` port; environment locking installs the program and replays it offline. The result is `["module systems", "committed"]`.

The compiler is also available directly against that prepared repository:

```bash
.venv/bin/mf check-program examples/typed/transaction.toml \
  --registry .mf/typed-example/repository --out .mf/type-report.json
.venv/bin/mf build-program examples/typed/transaction.toml \
  --registry .mf/typed-example/repository --out .mf/typed-build
```

Outputs include normal wheels, reviewable `generated.py`, `certificate.json` with the typed intermediate representation and checker result, and a `work-contract.json` for contributors. The certificate is a hash-bound checker report, not a machine-checked metatheoretic proof. Published programs remain ordinary module constructors and participate in existing graph composition and synthesis.

Runtime ownership guards additionally reject stale handles, incompatible nominal/primitive values, and conflicting moves. Nested checked programs preserve ownership when calling one another. **The guarantee ends at trusted Python operation implementations:** an adapter must honor its contract, return fresh ownership where promised, and manage its own failure cleanup. Normal-path linearity does not prove cleanup after exceptions, termination, or exactly-once external effects. Borrow scopes currently last for one synchronous operation call.

See the [checked-language guide](docs/checked-language.md) for the grammar, typing rules, trust boundary, and current limits. There is no general Python body checker, inferred lifetime system, generative abstract-type calculus, or numerical budget solver in this release.

## Whole systems, open modules, and agent contributions

For a knowledge system using Mari, retrieval algorithms may require an embedding module, a data layer, and an agent harness that itself requires a model contract. Those requirements should be module ports. The build selects providers, checks their relationships, and generates the Python wiring. An application imports the resulting system and supplies runtime inputs such as credentials and request data. Provider selection belongs to the build; service connections and execution belong to runtime.

An **open module** is a reusable fragment with exports and unfilled requirements. Link several fragments, publish the resulting subsystem with its remaining requirements, then close it in a later build. Nonrecursive mixins contribute operations through explicit dependency ports. The builder checks wiring, projections, renames, and initialization order. Python implementations use their supplied dependency objects; arbitrary globals are not automatically rebound.

Three different relationships matter: shared Python types, shared semantic identities such as an embedding space, and shared runtime instances such as a transaction context. Two embedding providers returning equal-sized arrays need not be compatible with the same index. Two modules using the same database driver need not share state. The intended build language makes these relationships explicit.

| Feature | Current status and direction |
| --- | --- |
| Published interfaces and named constructors | Implemented; constructors receive selected dependency modules. |
| Goal-directed composition | Bounded constructor synthesis plus provider selection for authored graphs; published open graphs participate in synthesis. |
| Shared types, indices, and instances | Nominal type checks, declared semantic-index constraints, and shared named graph nodes. |
| Open fragments and mixins | Nonrecursive partial linking, renaming, projection, and explicit dependency binding implemented. |
| Fixed generated Python wiring | Compiled wheels contain fixed imports and calls; runtime checks perform no selection. |
| Independent agent work contracts | Public/port contract slices and Protocol stubs generated; behavior still needs independent evidence. |

TOML is the build DSL. The [module build guide](docs/module-build.md) documents the implemented grammar, constraints, generated artifacts, and execution semantics. The earlier [whole-system design](docs/build-time-module-composition.md) records the research direction; recursive modules, general lifecycle elaboration, and synthesis of arbitrary graph topology remain future work.

## Build a real composed system

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python examples/knowledge_system.py --work-dir .mf/knowledge
```

Use an empty work directory. The example publishes Mari's unchanged MMR algorithm and small bindings to scikit-learn, SQLite, and Python's executor. It compiles an [open retrieval module](examples/modules/retrieval.toml), publishes it, and then builds a [complete system](examples/modules/knowledge.toml) with a cached embedding mixin and one shared data node. Finally it synthesizes a goal, locks and installs the complete environment, and executes two queries offline. The expected first hits are `dogs` and `python`.

This exercises real lexical vectorization and storage. The executor is a bounded execution harness; an LLM model/harness integration is not included. See [candidate provenance](adaptations/knowledge/README.md) and [offline wheelhouse instructions](docs/module-build.md#run-the-real-candidate-system).

The build commands are also directly available once providers are published:

```bash
.venv/bin/mf resolve-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository
.venv/bin/mf build-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository --out .mf/retrieval-build
.venv/bin/mf publish .mf/retrieval-build/index.json \
  --registry .mf/knowledge/repository
```

A build emits a publication index and wheels, reviewable `generated.py`, `contract.pyi`, a bounded `work-contract.json`, and build provenance. Compiled open graphs are ordinary published constructors, so they can be nested, contributed by independent publishers, and closed by the existing synthesizer. Ambiguous or incomplete resolution requires an explicit choice.

## Start with a goal

```toml
schema_version = 1

[goal]
name = "deduplicate-and-batch"
requires = { id = "workflows.batch_pipeline", version = "1" }
capabilities = ["deduplicate", "batch"]

[policy]
allowed_effects = ["caller-iteration"]
```

The repository contains a plain batching constructor and a deduplicating batching constructor. The synthesizer selects a suitable constructor, then finds implementations of its `chunker` and `deduplicator` requirements. The goal names neither the constructor nor a Python library.

The real example uses preserved source from **more-itertools 11.1.0**, **boltons 25.0.0**, and the copied **Mari** project. More-itertools initially supplies one complete program. Publishing Boltons into the existing iterator family expands the choices to four compositions; the earlier program still replays. All four return `[[3, 1], [2, 4]]` for the records `[3, 1, 3, 2, 1, 4]` with batch size two. A separate freshness goal discovers Mari's recency algorithm and returns `0.5` for one exponential half-life.

## Run the real example

Python 3.11 or later:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/prepare_candidates.py
.venv/bin/python examples/real_world.py --work-dir .mf/demo
```

Use an empty work directory. The example builds and publishes independent contributions, synthesizes and executes every valid iterator composition, verifies the older program still works after publication, and runs the Mari goal. It writes the repository, wheels, solutions, program locks, and `report.json` below that directory. It does not import the original packages to execute the generated programs.

The same path is available through the CLI:

```bash
.venv/bin/mf synthesize examples/goals/deduplicate-and-batch.toml \
  --registry .mf/demo/repository --out .mf/solutions.json
.venv/bin/mf lock-assembly .mf/solutions.json --choice 0 \
  --registry .mf/demo/repository --out .mf/program.lock.json
.venv/bin/mf execute .mf/program.lock.json \
  --registry .mf/demo/repository --export run --args '[[3,1,3,2,1,4],2]'
```

Ambiguity is explicit: `--choice` records which program was selected. A goal can request `[preferences] selection = "min_artifacts"` to rank by its exact artifact closure; ties remain choices. Search depth, candidate, state, and solution limits are recorded. Exhausting a limit never silently certifies a unique program.

## Lock the complete environment

`lock-env` resolves ordinary third-party wheel dependencies and includes the execution runtime. `sync` verifies the pinned interpreter and wheel hashes, installs without network access, and checks the resulting dependencies. `exec` runs the program in that environment without contacting the repository.

```bash
.venv/bin/mf lock-env .mf/program.lock.json \
  --registry .mf/demo/repository --out .mf/environment
.venv/bin/mf sync .mf/environment/environment.lock.json --target .mf/program-python
.venv/bin/mf exec .mf/environment/environment.lock.json \
  --target .mf/program-python --export run --args '[[3,1,3,2,1,4],2]'
```

To reproduce the example with fully offline resolution as well, first download its external runtime dependency, then use a fresh directory:

```bash
.venv/bin/pip download --only-binary=:all: --no-deps packaging==26.3 -d .mf/wheelhouse
.venv/bin/python examples/real_world.py --work-dir .mf/offline-demo \
  --offline-environment --wheelhouse .mf/wheelhouse
```

## Contribute to an existing family

A contribution declares the existing family header and its own publisher identity:

```toml
schema_version = 1

[family]
name = "ranking"
version = "1.0.0"
description = "Reusable ranking algorithms"

[publisher]
name = "alice"

[source]
root = "src"
package = "alice_ranking"

[discovery]
public = true

[contributions]
include = ["members/**/*.toml"]
```

A local member `temporal.decay` is published as `alice.temporal.decay`. Contributor fragments add interfaces, capability declarations, version overrides, and module requirements without editing an expanded global inventory. For an existing family, copy its exact header and context from repository metadata; each publisher maintains its own contribution manifest. See the [independent iterator contributions](families/iterators/README.md).

```bash
.venv/bin/mf init path/to/project --package alice_ranking \
  --family ranking --publisher alice --out families/ranking/family.toml
.venv/bin/mf plan-build families/ranking/family.toml --member alice.temporal.decay
.venv/bin/mf build families/ranking/family.toml --member alice.temporal.decay
```

The compiler derives shared source dependencies, keeps common nominal Python types in shared cells, and emits ordinary wheels for selected members and their reachable implementation dependencies. Multiple source roots, grouped exports, interface-only publications, per-member versions, and persistent analysis caching are supported. The cache is coarse: an analysis input change triggers reanalysis, while unchanged selected wheels retain their exact bytes.

## Module constructors and mixins

A constructor declares the interfaces it needs and the interface it produces. Its Python factory receives the selected modules by name and returns the exports of the composed module. For example, the existing workflow contribution contains:

```toml
[[members]]
id = "deduplicated_batches"
kind = "functor"
symbol = "module_workflows.iterables:deduplicated_batches"
provides = { id = "workflows.batch_pipeline", version = "1" }
requires = { chunker = { id = "iterators.chunker", version = "1" }, deduplicator = { id = "iterators.unique", version = "1" } }
capabilities = ["deduplicate", "batch"]
effects = ["dependency-effects"]
```

Named requirements, same-interface wrappers such as retry, grouped exports, shared type libraries, and order-sensitive constructor composition are implemented. **Nonrecursive module mixin linking is now implemented through `build-module`.** Its TOML graph connects requirements, merges exports explicitly, and lowers the result to a normal constructor. A named node is instantiated once per graph invocation; separate nodes invoke factories separately. Recursive hole filling and implicit rebinding of existing Python globals remain unsupported. See the [module build guide](docs/module-build.md), [updated mixin handoff](docs/mixins-handoff.md), [contract guide](docs/contracts.md), and [synthesis guide](docs/synthesis.md).

## Serve the repository

```bash
export MF_PUBLISHERS='{"alice-local-token":{"publisher":"alice"},"bob-local-token":{"publisher":"bob"}}'
.venv/bin/mf serve --registry .mf/repository --port 8042
```

From another shell:

```bash
export MF_TOKEN='alice-local-token'
.venv/bin/mf publish dist/ranking/index.json --registry http://127.0.0.1:8042
.venv/bin/mf interfaces --registry http://127.0.0.1:8042
```

Read access is public; publication tokens identify publishers. By default Alice and Bob can contribute their own members to any family. Optional token family restrictions narrow that permission. Existing family headers and releases are immutable. Interface IDs and wheel distribution names have publisher ownership; exact existing shared artifacts can be reused. The HTTP client verifies selected metadata and content-addressed objects. See the [repository API and operation guide](docs/repository.md).

## Research and practical boundaries

The [research proposal](docs/RESEARCH.md), [module theory review](docs/research-theory.md), [distribution review](docs/research-distribution.md), and [build-system review](docs/research-build-systems.md) develop the basis in ML signatures, functors, sharing constraints, Backpack, family polymorphism, and modern build systems. The [synthesis guide](docs/synthesis.md) distinguishes the implemented search calculus from the larger research direction.

This implementation synthesizes compositions of published modules and constructors. It does not invent arbitrary Python algorithms or prove capability claims. Interface call shapes are checked at linking; shared type identities are enforced; effects and behavioral laws remain declared obligations. Atomic import withholds the resulting binding until preparation and linking succeed, but cannot roll back arbitrary Python effects. Unsupported Python source requires an explicit, reviewed adaptation; the iterator projections preserve selected upstream definitions and record omissions and hashes.

The service is a working single-node repository. Internet-scale throughput, distributed trust, general higher-order module typing, recursive linking, automatic resource lifecycle management, and autonomous adapter generation remain unvalidated research work. Semantic indices enforce agreement on declarations, not the truth of an embedding or storage claim. Measurements for thousands of definitions concern local discovery and builds, not a claim of operating a global ecosystem.

Run validation with `.venv/bin/pytest -q` and `.venv/bin/ruff check src tests scripts examples`. See [validation](docs/VALIDATION.md), [real candidate provenance](adaptations/README.md), and [build guide](docs/build-system.md).
