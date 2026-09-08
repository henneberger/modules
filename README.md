# Module Families

**A module system for software projects built by millions of agents.**

Our goal is to let millions of agents contribute to one software project through small, typed, independently composable units of work. An agent should be able to work on a ranking algorithm, storage adapter, or retrieval subsystem against a precise local contract. Other agents should be able to discover that contribution, check whether it fits, and compose it into a larger program.

This requires more than distributing smaller packages. We need to express what a component needs, which types and resources it shares with its neighbors, how its behavior can be extended, and which combinations are valid. Module Families brings ideas from ML module systems into Python's build and distribution layer, with ordinary Python as the implementation language.

The project combines a build system, a checked composition language, and a repository. Its central idea is that the structure of software can become the structure of collaboration: types describe the work, module requirements expose what is missing, and composition turns independent contributions into a running system.

A [recorded working example](docs/live-composition-validation.json) gives one real
coding agent six published component cards. It writes a
[TOML knowledge system](examples/modules/agent-authored-knowledge.toml), using
existing Python implementations, which passes evaluation and is selected by the
build system. This demonstrates the central loop; million-agent operation remains
the scaling target.

## The problem: building a knowledge base together

Consider an enterprise knowledge base answering questions across product manuals, support tickets, internal documents, and source code. A useful system might need:

- Connectors and parsers for different sources, with document identity and provenance preserved.
- Chunking and embedding strategies suited to different content.
- Storage, retrieval, filtering, reranking, and citation assembly.
- An execution harness that can use a model, retrieve additional evidence, and manage work.
- Caching, retries, evaluation, and resource management around those operations.

There may be thousands of useful algorithms within each category. A team working on legal documents may contribute a different chunker; another may contribute a multilingual ranker; another may adapt an existing database. A single owner should not have to maintain all of these implementations. Nor should every improvement require editing one enormous application or publishing its entire dependency ecosystem again.

Suppose one agent improves query embedding while another rebuilds the index. Both implementations return arrays of the expected length, and both pass their own API tests. The combined system can still produce meaningless retrieval if the arrays belong to different embedding spaces. This is a practical constraint: Azure AI Search's vector-query guidance calls for using the same embedding model for queries and indexed documents. [Vector query documentation](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-query).

Or suppose ingestion and retrieval both require a document store. Choosing the same database library twice does not mean they see the same store instance. With two in-memory stores, ingestion succeeds and retrieval sees nothing.

These are relationships between components. They belong in the description of the system, where a build can check them, instead of depending on every contributor remembering them.

## Why API contracts are insufficient on their own

An ordinary API contract describes how to call something. It often leaves the relationships needed to assemble a complete system implicit.

```python
embed(text: str) -> list[float]
search(vector: list[float]) -> list[Document]
```

These shapes do not answer:

| Question | Why it matters | What a richer module contract can express |
| --- | --- | --- |
| Were these vectors produced in the index's embedding space? | Equal dimensions do not imply comparable coordinates. | A shared semantic identity for the space. |
| Does the parser produce the document type the store accepts? | Similar fields do not establish the same nominal type. | Shared exported types or explicit nominal value identities. |
| Do ingestion and retrieval use the same store? | The same implementation can create separate state. | An explicit shared node and instance constraint. |
| Can this retrieval module use any suitable embedding provider? | Hardcoded imports prevent independent replacement. | An unfilled requirement for an embedding signature. |
| Can we enrich embedding with a cache? | Every consumer should see the selected enrichment consistently. | A wrapper module connected through dependency ports. |
| Can the composition reuse a committed transaction? | A callable signature alone does not describe resource consumption. | Ownership modes and checked operation sequences. |
| Does this composition fit the allowed effects? | A compatible function may still declare network or storage effects. | Declared operation effects and composition bounds. |

API specifications can be extended to encode some of this. The important step is giving those declarations composition rules and a checker that uses them. Otherwise they remain prose obligations that every agent must rediscover and reconcile.

APIs still connect the running system to users and external services. Module contracts describe how to construct that system. An adapter for a remote API can itself be a module with explicit dependencies, types, and effects.

## Types make independent work composable

Types are useful here because they carry assumptions across a boundary. They let a contributor depend on a statement such as “I receive documents of this identity” without reading every implementation that might produce them.

A module **signature** describes its exported operations and types. A **module constructor**, traditionally called a functor, takes modules satisfying required signatures and produces another module. Its requirement can say:

```text
I need an EMBEDDING and a DOCUMENT_STORE.
Given those, I provide RETRIEVAL.
```

That statement can be published before the concrete embedding and storage implementations have been chosen. A subsystem can be checked, reused, and extended while some of its requirements remain open.

A useful system contract expresses several kinds of agreement:

| Mechanism | Example | Meaning |
| --- | --- | --- |
| Call shape | `search(query, limit)` | The required exports and parameter shapes match. |
| Shared Python type | `parser.Document = store.Document` | Declared type relationships are checked; runtime linking checks actual type-object identity. |
| Nominal value type | `knowledge.document@1` | Checked compositions treat the same explicit identity as the same value type; local aliases may differ. |
| Semantic index | `embedding.Space = index.Space` | Opaque declared identities must agree, for example on encoder revision, preprocessing, and metric. |
| Shared instance | `retrieval.data = harness.data` | Both slots are wired to the same named graph node or port. |
| Usage and effects | Borrow a session, then move it into commit. | The checked language accounts for ownership and declared operation effects. |

Types also make the search space intelligible. An agent looking for a reranker should see candidates that accept its document type and preserve the identities needed for citations. An integration agent should reject an incompatible embedding space before spending time evaluating retrieval quality. Types establish which compositions make sense; evaluations distinguish the useful ones.

The longer-term direction is to publish reusable vocabularies of documents, chunks, citations, model capabilities, resource protocols, and permitted effects. A contributor can then improve one operation while preserving the relationships the rest of the system depends on.

## Designing for millions of agents on one project

The scaling principle is **bounded local knowledge and hierarchical composition**. A project can have a very large contribution ecosystem while each task and each executable uses a much smaller slice of it.

A ranking contributor should need the ranking contract, relevant shared types, permitted dependencies, and an evaluation task. It should not need the implementation of the billing service, every document connector, or every competing ranker. An integration agent should be able to select a published retrieval subsystem without reopening all of its internal decisions.

The intended contribution loop is:

1. Define a subsystem's public signature and the signatures it may require.
2. Give contributors the relevant contract slice. The graph builder emits `work-contract.json` and Python Protocol stubs; the checked-program builder emits its typed work contract.
3. Implement or adapt ordinary Python, or compose existing modules. Contributions can live under separate publisher identities in one family.
4. Check and publish each contribution independently. Checked `.mfl` compositions can be checked against interfaces before providers exist.
5. Compose validated contributions into a subsystem and publish that subsystem with any remaining requirements.
6. Select and lock a complete composition for a particular application or experiment.

Imagine a million-agent effort organized around a shared knowledge platform. Some groups improve ingestion for particular document formats. Others explore ranking algorithms, storage backends, model harnesses, or evaluation methods. Within each group, agents work against smaller contracts. Successful compositions become reusable subsystems, which become inputs to the next level of composition.

The project is shared through its contracts and contribution graph. Agents do not all need to edit the same checkout or attend to the same global stream of changes. A subsystem becomes another module: its consumer sees the exported contract and remaining requirements, while the exact implementation closure stays available for building and audit.

This is also why finer-grained publication matters. A new algorithm should become available as soon as it satisfies its contribution contract. It should not wait for the release schedule of every other algorithm in the family. A consumer should be able to try it in one selected composition while everyone else continues using their locked programs.

Several design choices support this direction:

| Coordination problem | Architectural response |
| --- | --- |
| Everyone edits a global inventory. | Discover source locally and publish independent contribution manifests. |
| One family owner becomes the ecosystem's bottleneck. | Let independent publishers contribute implementations and new dependencies. |
| Every agent needs the entire source tree. | Give each task the relevant signatures, shared types, and open requirements. |
| Every consumer must inspect every algorithm. | Discover by contract and capability; reuse composed subsystems as selection units. |
| A new provider changes everyone's application. | Publish immutable versions and lock each selected program. |
| Every algorithm ships its whole library. | Build only the selected members and their reachable implementation dependencies. |

The hardest work moves into designing good boundaries. A useful contract must be small enough to understand and expressive enough to capture the relationships that matter. Shared type libraries prevent every contributor from inventing another incompatible document model. Open modules let contributors leave legitimate choices to downstream builders. Hierarchical composition lets local progress accumulate into a much larger system.

Agents can make this level of explicit structure practical. They can inspect contracts, find candidate dependencies, generate adaptations, and propose compositions at a granularity that would be tedious to maintain manually. The build system supplies the common rules for accepting and connecting those contributions. The ambition is a project that continuously explores many implementations while retaining coherent, reproducible executable systems.

## A module family is an ecosystem

A family groups contributions around a problem domain and its context: retrieval, payment processing, embeddings, parsing, or data storage. It can contain interfaces, reusable type libraries, algorithms, adapters, and constructors.

A publisher owns its contributions, not the entire family. Two independent publishers can add implementations of the same interface. An interface-only release can establish a contract before implementations arrive. New members and member versions can be published incrementally, while existing program locks retain their original selections.

For example, a ranking family can contain `alice.temporal_decay` and `bob.diversity_ranker`. Alice does not need to acquire Bob's code or republish it. A third publisher can contribute a constructor that combines suitable ranking components.

Families also provide context for discovery, while individual members declare capabilities and requirements. The larger repository should connect these declarations to evaluations and provenance, so agents can discover both what fits and what has worked for comparable problems.

## Three authoring layers, ordinary Python execution

| Layer | Authored files | Role |
| --- | --- | --- |
| Implementation | `.py` | Existing algorithms, adapters, factories, and actual service operations. |
| Packaging and module graphs | `.toml` | Discovery, publication, signatures, open ports, provider selection, links, sharing, and policies. |
| Checked operation composition | `.mfl` plus a TOML manifest | Value types, ownership transfers, borrows, branches, and declared effect bounds. |

The build emits ordinary Python wheels, fixed generated wiring, and provenance. Selected modules are initialized in the compiled order. Running a compiled module does not ask the repository to select providers again.

JSON indexes, locks, checker reports, and work contracts are generated artifacts. An expanded inventory is not something contributors maintain by hand. Source authoring is TOML, with `.mfl` where operation-level checking is needed. This research release provides no compatibility paths for older manifests or repository schemas.

### Compose a knowledge system at the build layer

The repository includes an executable knowledge-retrieval example using:

- Mari's preserved maximal marginal relevance algorithm for diversified ranking.
- scikit-learn's `HashingVectorizer` for lexical vectors.
- A caching wrapper around the embedding module.
- SQLite for documents.
- `ThreadPoolExecutor` as an execution harness.

The [open retrieval module](examples/modules/retrieval.toml) selects a ranker and retrieval constructor, leaving embedding and data as requirements. These are excerpts from that manifest:

```toml
[ports.embedding]
requires = { id = "knowledge.embedding", version = "1" }

[ports.data]
requires = { id = "knowledge.documents", version = "1" }

[links]
"retrieval.embedding" = "embedding"
"retrieval.data" = "data"
"retrieval.ranker" = "ranker"

[exports]
search = "retrieval.search"

[indices]
Space = "embedding.Space"
```

Publishing this module makes retrieval reusable without choosing storage and embedding for every future consumer. The [complete system](examples/modules/knowledge.toml) then selects providers and connects the remaining ports:

```toml
[links]
"embedding.base" = "base"
"retrieval.embedding" = "embedding"
"retrieval.data" = "data"
"harness.retrieval" = "retrieval"
"harness.data" = "data"

[constraints]
same_instance = [["retrieval.data", "harness.data"]]
same_index = [["embedding.Space", "retrieval.Space"]]

[exports]
run = "harness.run"
close = "data.close"
```

The harness and retrieval module share one data node. Retrieval sees the cached embedding module. The chosen embedding-space identity propagates through the composition. This wiring is part of the built module, so the application supplies documents and queries without reconstructing the dependency graph.

A model-backed knowledge agent extends the same architecture with a model contract and a harness that requires it. A general model contract can describe the operations the harness needs, while independent contributors supply providers, tool adapters, or execution strategies. The included example demonstrates the composition mechanism using lexical vectors and a Python executor; the transaction example below demonstrates value-level typing.

### Extend behavior with mixins

A cached embedding module requires `base: EMBEDDING` and provides `EMBEDDING`. Consumers bind to its enriched result. This allows caching to be contributed separately from both the encoder and its consumers.

The same pattern can express retry, tracing, normalization, or authorization adapters. Order matters: retrying an entire ingestion operation can behave differently from retrying only its remote read. The module graph records the selected order and dependencies; satisfying the same interface does not imply equivalent behavior.

The current builder supports **nonrecursive mixin linking** through explicit ports, partial publication, nesting, export merging, projection, and renaming. A named node is instantiated once per graph invocation; separate nodes invoke factories separately. Cycles and implicit rebinding of Python globals are unsupported. See [module composition](docs/module-build.md).

### Check resource use inside a composition

Graph wiring alone cannot establish that a transaction is consumed correctly. The `.mfl` language adds a small, separately checked operation language above ordinary Python providers.

The real [transaction contract](families/transactions/interfaces.toml) declares a linear session:

```text
begin()                                  -> Session
put(borrow Session, share Text, share Text) -> ()
read(borrow Session, share Text)          -> Text
commit(move Session)                     -> Text
abort(move Session)                      -> ()
```

The [checked program](examples/typed/transaction.mfl) is:

```python
# .mfl: key and value are inputs declared by the public signature.
session = store.begin()
store.put(session, key, value)
observed = store.read(session, key)
receipt = store.commit(session)
return observed, receipt
```

Reading after commit is a build error: the session has moved. Returning without consuming the session is also a build error. Neither rejection needs a concrete SQLite provider to exist.

The declaration that enables this is ordinary TOML. This excerpt names an opaque value type and a consuming operation:

```toml
[interfaces.typing.types]
Session = { id = "transactions.sqlite.session@1", usage = "linear", representation = "opaque" }
Text = { id = "python.str", usage = "shared", representation = "str" }

[interfaces.typing.operations]
commit = { parameters = { session = { type = "Session", mode = "move" } }, returns = ["Text"], effects = ["sqlite"] }
```

Shared values may be reused or ignored. Affine owners may be transferred at most once and may be discarded. Linear owners must be transferred exactly once on each normally returning path, including by returning ownership to the caller. A borrow lends access for one synchronous operation and does not consume ownership; it need not be read-only.

The compiler checks each terminal branch and unions the declared effects of its calls. Generated runtime guards also reject stale handles and incompatible values. Python operation implementations remain trusted, including their effect declarations, ownership promises, and failure cleanup. The checker does not prove cleanup after exceptions, termination, or exactly-once external effects.

Ordinary Python implements the operations; `.mfl` expresses their checked composition. See the [language and typing rules](docs/checked-language.md) for the supported grammar and ownership model.

## Synthesize a program from a goal

The objective is to automatically select and compose modules according to what they provide and require. The current implementation does this by searching **published module constructors and their requirements**.

A goal from the included iterator example is:

```toml
schema_version = 1

[goal]
name = "deduplicate-and-batch"
requires = { id = "workflows.batch_pipeline", version = "1" }
capabilities = ["deduplicate", "batch"]

[policy]
allowed_effects = ["caller-iteration"]
```

The goal names no library. The synthesizer finds a constructor providing the result interface, then recursively fills its chunker and deduplicator requirements with compatible providers. Published open graphs and checked programs participate as constructors too.

The real example adapts more-itertools and boltons. Publishing another provider into the existing family expands the available compositions without rewriting the workflow. Earlier locked programs continue to select their original artifacts.

This is bounded constructor synthesis, not arbitrary algorithm invention. Graph builds select providers for authored topology; synthesis can nest published constructors. Capability labels guide selection but do not prove behavioral laws; aggregating a dependency's labels does not prove that its parent exposes the claimed behavior. Multiple valid compositions remain an explicit choice; search limits remain visible. See [synthesis semantics](docs/synthesis.md).

## Associate types with the modules that define them

Associated types and substitution are executable in both the graph builder and checked `.mfl` operation bodies. An embedding signature can declare an abstract `Space`; a provider supplies its concrete witness; a cache exports `base.Space`. A typed vector result can then be described as `VectorBatch[Space]`, rather than an unrelated array type.

An open module can require `query.Space = index.Space` without selecting either provider. Its published constructor carries that equation forward. When another graph supplies providers, substitution checks their witnesses and rejects distinct spaces—even if their arrays have identical dimensions. The same mechanism connects a retriever's document IDs to its data store's domain.

```toml
# Fragment of an open graph; paths refer to its declared nodes or ports.
[associated.types]
Space = { from = "embedding.Space", kind = "identity" }

[constraints]
same_associated = [["query.Space", "index.Space"]]
```

The [associated-type guide](docs/associated-types.md) explains the term language, typed projection checks, and residual equations. The real example reuses the knowledge-system providers:

```bash
.venv/bin/python examples/associated_system.py --work-dir .mf/associated
```

Run it after installing the project below. The checker also substitutes associated types inside `.mfl` calls and nested owned returns. Two ports remain distinct until an explicit sharing equation relates them: calling one from the other cannot silently manufacture equality. Generated fresh type identities remain language research.

## Close the contribution loop

The contribution protocol makes the coordination model executable. A missing provider becomes a contribution draft; an integrator adds acceptance cases; a contributor builds a candidate in a staging repository; evaluation binds observations to the exact program and environment; an explicit acceptance command publishes the tested contribution.

```bash
.venv/bin/python examples/contribution_system.py --work-dir .mf/contribution-demo
```

Run this after installing the project as shown below. The example composes the existing knowledge-base providers twice. One candidate has compatible APIs but gives ingestion and retrieval different SQLite stores. Its behavioral cases fail, and publication through the acceptance command is rejected. The correctly shared candidate passes, is published, and fills the original synthesis goal.

The task is a small TOML contract with an exact interface, declared capability/effect requirements, and explicit input/output cases. It is prepared before a complete system provider exists. Each case runs in a fresh locked worker process with a timeout. Evidence records the task, program, environment, and observations. The integrated evaluator signs its own passing observations; evidence-aware synthesis then requires a trusted observation for the exact selected composition and executable environment.

See the [contribution workflow](docs/contributions.md) for the contract grammar, CLI commands, evidence semantics, and working example. The [module calculus design](docs/module-calculus.md) distinguishes implemented associated-type rules from remaining language research.

## From a missing contract to agent-authored software

An agent worker receives a small immutable task packet: the required signature,
capabilities, effect policy, and acceptance cases. An operator-configured coding
agent writes ordinary Python and a TOML manifest. The worker builds the artifact
and publishes it to staging. A separately authorized evaluator resolves its
accepted dependencies, builds a locked environment, runs the cases, signs passing
observations, and admits the exact contribution to the repository.

For a knowledge-ingestion team, the packet might ask for document chunking that
preserves source identity. It need not include the search service, the billing
service, or a checkout of every competing algorithm. The same protocol accepts
an open graph or checked `.mfl` program, so an integration agent can contribute a
composition rather than another implementation of its dependencies.

```bash
# Uses your authenticated local Codex CLI to compose existing providers in TOML.
.venv/bin/python examples/agent_composition.py --work-dir .mf/agent-composition

# Smaller task: adapt standard-library textwrap while preserving document IDs.
.venv/bin/python examples/agent_contribution.py --work-dir .mf/agent-demo
```

The composition example supplies a bounded set of published contracts and cards.
The agent writes the module graph; Mari ranking, scikit-learn embeddings, SQLite
storage, and the query harness remain existing Python implementations. Evaluation
checks the resulting knowledge system before publication.

The [recorded repair run](docs/live-repair-validation.json) starts with an
incorrect chunker. Evaluation rejects it; a real agent reads the failure feedback
and publishes a corrected version. Rejected and repaired artifacts retain distinct
immutable identities, and only the passing repair reaches the accepted repository.

The [campaign coordinator](docs/campaigns.md) accepts a TOML task DAG and a final
system goal. Tasks become eligible after their prerequisites are accepted; each
agent has bounded attempts, leases, and evaluation feedback. The coordinator
repeats contribution and evaluation rounds, then synthesizes and locks a unique
complete system. Acceptance cases are supplied contracts, not tests invented by
candidate code to approve itself.

A goal can require evidence as well as types:

```toml
[goal]
name = "source-preserving-ingestion"
requires = { id = "knowledge.chunker", version = "1" }
capabilities = ["source-preserving-chunking"]

[evidence]
tasks = ["<sha256 of the prepared acceptance contract>"]
evaluators = ["knowledge-ci"]
```

The [evidence guide](docs/evidence-selection.md) explains how observations bind
to a whole composition, including its supplied dependencies. Changing a vector
provider, Python wheel, interpreter, or runtime can require reevaluation. Attestations support Ed25519 public evaluator identities and HMAC within an
operator trust domain. Consumers configure trusted public keys explicitly. Finite passing cases establish those observations,
not a proof of arbitrary Python behavior.

The durable queue supports remote authenticated workers, atomic claims, lease
renewal, stale-worker fencing, immutable prerequisite DAGs, and crash recovery.
A million task records have been exercised with eight real worker processes.
See the [coordination measurements](docs/coordination-scale.md) for throughput,
resource use, and the distinction between records and active agents. A Python
virtual environment isolates dependencies; untrusted candidate execution still
requires an operator-provided OS sandbox.

## Run the examples

Python 3.11 or later:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Each example needs an empty work directory. Environment locking may download third-party wheels; execution then uses the installed, locked environment.

**Knowledge retrieval:** build and publish an open retrieval subsystem, compose the full system, synthesize a goal, and execute two queries offline.

```bash
.venv/bin/python examples/knowledge_system.py --work-dir .mf/knowledge
```

Expected first hits: `dogs` and `python`. See [candidate provenance](adaptations/knowledge/README.md) and [offline wheelhouse preparation](docs/module-build.md#run-the-real-candidate-system).

**Separate checking and ownership:** publish interfaces, check the program before any store provider exists, reject two invalid programs, then publish SQLite and synthesize the executable composition.

```bash
.venv/bin/python examples/typed_system.py --work-dir .mf/typed
```

Expected result: `["module systems", "committed"]`. Its rejected examples demonstrate use after move and an unconsumed linear resource.

**Independent contributions and incremental selection:** prepare preserved upstream projections, publish alternative providers, and run the resulting iterator compositions and a Mari freshness goal.

```bash
.venv/bin/python scripts/prepare_candidates.py
.venv/bin/python examples/real_world.py --work-dir .mf/iterators
```

The iterator compositions return `[[3, 1], [2, 4]]` for `[3, 1, 3, 2, 1, 4]` with batch size two. See [adaptation provenance](adaptations/README.md).

### Build and publish directly

After the knowledge example has prepared its repository:

```bash
.venv/bin/mf resolve-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository
.venv/bin/mf build-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository --out .mf/retrieval-build
.venv/bin/mf publish .mf/retrieval-build/index.json \
  --registry .mf/knowledge/repository
```

After the typed example has prepared its repository:

```bash
.venv/bin/mf check-program examples/typed/transaction.toml \
  --registry .mf/typed/repository --out .mf/type-report.json
.venv/bin/mf build-program examples/typed/transaction.toml \
  --registry .mf/typed/repository --out .mf/typed-build
```

Both builders emit normal publication indexes and wheels, reviewable generated Python, and contributor work contracts. Graph builds also emit Protocol stubs and graph provenance. Checked builds emit `certificate.json`: a hash-bound checker report and typed intermediate representation, not a machine-checked metatheoretic proof.

### Select and lock the complete executable

After the iterator example:

```bash
.venv/bin/mf synthesize examples/goals/deduplicate-and-batch.toml \
  --registry .mf/iterators/repository --out .mf/solutions.json
.venv/bin/mf lock-assembly .mf/solutions.json --choice 0 \
  --registry .mf/iterators/repository --out .mf/program.lock.json
.venv/bin/mf lock-env .mf/program.lock.json \
  --registry .mf/iterators/repository --out .mf/environment
.venv/bin/mf sync .mf/environment/environment.lock.json --target .mf/program-python
.venv/bin/mf exec .mf/environment/environment.lock.json \
  --target .mf/program-python --export run --args '[[3,1,3,2,1,4],2]'
```

The program lock fixes the chosen composition. The environment lock includes ordinary third-party wheel dependencies and the execution runtime. Synchronization verifies interpreter constraints and wheel hashes, installs offline, and checks dependencies. Execution needs no repository connection. Environment resolution uses pip and permits wheels only.

Atomic import exposes the resulting module binding only after preparation and linking succeed. It cannot roll back arbitrary Python import or initialization effects. See [contracts](docs/contracts.md) and [environment operations](docs/build-system.md).

## Adapt existing Python and contribute smaller units

Mari is a migration candidate, not the definition of the module system. Its copy under [projects/mari-kit](projects/mari-kit) supplies existing algorithms. Independent examples use other libraries and ordinary Python adapters.

The build discovers supported source definitions, derives reachable dependencies, and emits selected member wheels plus shared source cells. Shared cells preserve common implementation and nominal Python type identity. Consumers can select one algorithm without installing every unrelated algorithm in the source project.

A contribution manifest can stay small even when its source inventory is large:

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

This is an illustrative manifest for a source project named `alice_ranking`. For an existing family, use its exact immutable header and context. Contributor fragments add member-specific interfaces, capabilities, requirements, and versions without editing a generated global inventory.

```bash
.venv/bin/mf init path/to/project --package alice_ranking \
  --family ranking --publisher alice --out families/ranking/family.toml
.venv/bin/mf plan-build families/ranking/family.toml --member alice.temporal_decay
.venv/bin/mf build families/ranking/family.toml --member alice.temporal_decay \
  --out dist/ranking
```

The commands assume that project actually defines the selected member. Multiple source roots, grouped exports, interface-only publications, and per-member versions are supported. Analysis caching is currently coarse: changed inputs trigger source reanalysis, while unchanged selected artifacts retain their bytes.

Extraction has limits. Dynamic or otherwise unsupported Python requires an explicit adaptation; the tool does not claim to turn every library into sound independent modules automatically. Adapters expose dependency ports where existing code does not already have them. See the [build guide](docs/build-system.md), [migration evidence](docs/migration-validation.md), and [independent iterator contributions](families/iterators/README.md).

## Run a shared repository

The local and HTTP repositories use the same publication and selection model. This local development example authorizes two independent publishers:

```bash
export MF_PUBLISHERS='{"alice-local-token":{"publisher":"alice"},"bob-local-token":{"publisher":"bob"}}'
.venv/bin/mf serve --registry .mf/repository --port 8042
```

From another shell, after building Alice's contribution:

```bash
export MF_TOKEN='alice-local-token'
.venv/bin/mf publish dist/ranking/index.json --registry http://127.0.0.1:8042
.venv/bin/mf interfaces --registry http://127.0.0.1:8042
```

Reads are public. Publication tokens identify publishers; optional family restrictions narrow where a publisher may contribute. Family headers and releases are immutable. Interface IDs and wheel distribution names have publisher ownership, while exact existing shared artifacts may be reused. The client verifies selected metadata and content-addressed objects.

Repository discovery uses SQLite/FTS5 metadata and a content-addressed artifact store. Resolution can inspect metadata before fetching the selected artifact closure. See the [repository protocol and operations](docs/repository.md).

## Research direction and implementation status

The project draws on ML signatures, functors, abstract types, and sharing; module families and mixin composition; and separate checking above an existing implementation language. [Backpack](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf) is an important precedent for the latter. [Linear Haskell](https://arxiv.org/abs/1710.09756) informs the interest in combining unrestricted values with ownership-sensitive composition. This implementation uses a smaller calculus and does not inherit those systems' soundness results.

The research direction is a software ecosystem where an agent can begin with a desired system, find its missing contracts, delegate those contracts to other agents, and assemble the resulting contributions. Richer module types, reusable protocols, evaluation-backed selection, and distributed publication are the foundations for that ecosystem.

The implementation includes the build and repository core, open module composition, associated-type substitution in graphs and checked operations, bounded constructor synthesis, ownership/effect checks, durable remote worker coordination, staged contributions, and authenticated evidence-aware selection. Local repository and queue services use SQLite; federated discovery combines independent repositories through bounded queries. Operation with millions of simultaneously active agents remains unmeasured. Python providers remain trusted. Full generative module typing, arbitrary program synthesis, and automatic lifecycle reasoning remain research work.

Local scale measurements include 10,000 definitions across 1,000 contribution files with a 21-line root TOML, and a copied Mari workload with 988 public definitions. These measure compact authoring and selective builds, rather than concurrent agent capacity.

The 0.7.0 validation includes the agent-authored knowledge graph, generic checked operations, contribution campaigns, federated discovery, and authenticated evaluation. Exact test counts, wheel checks, and measured scopes are recorded in the current validation report. Historical 0.5.0 and 0.4.0 reports retain the contribution-loop and SQLite evidence. Historical reports cover the composed knowledge system, copied Mari migration, and local source-scale workloads. These are separate measurements with their scopes recorded in [validation](docs/VALIDATION.md).

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts examples
.venv/bin/python -m build --wheel
```

| Read next | What it covers |
| --- | --- |
| [Research proposal](docs/RESEARCH.md) | Hypotheses and broader research agenda. |
| [Module theory](docs/research-theory.md) | Signatures, functors, sharing, families, and composition foundations. |
| [Distribution research](docs/research-distribution.md) | Software publication and ecosystem scale. |
| [Build-system research](docs/research-build-systems.md) | Adaptation, dependency extraction, and incremental builds. |
| [Module graph guide](docs/module-build.md) | Open modules, mixins, sharing constraints, generated wiring, and limits. |
| [Checked-language guide](docs/checked-language.md) | Grammar, nominal types, resource rules, effects, and the trusted Python boundary. |
| [Contribution workflow](docs/contributions.md) | Missing requirements, task contracts, evaluation, and accepted publication. |
| [Associated-type guide](docs/associated-types.md) | Executable type terms, graph substitution, and published sharing obligations. |
| [Module calculus design](docs/module-calculus.md) | Broader calculus, remaining language features, and implementation milestones. |
| [Synthesis guide](docs/synthesis.md) | Constructor search, ambiguity, policies, and program locks. |
| [Repository guide](docs/repository.md) | Publication, authentication, and artifact retrieval. |
| [Federation](docs/federation.md) | Independent repository shards, bounded discovery, and immutable identity conflicts. |
| [Campaigns](docs/campaigns.md) | TOML task DAGs, agent workers, evaluation feedback, and final synthesis. |
| [Evidence selection](docs/evidence-selection.md) | Exact-composition observations and trusted public evaluator identities. |
| [Coordination measurements](docs/coordination-scale.md) | Million-record workloads, active worker counts, and readiness indexing. |

Licensed under [Apache-2.0](LICENSE).
