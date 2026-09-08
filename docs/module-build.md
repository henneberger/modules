# Build and publish open Python modules

Available in 0.3.0. `resolve-module` checks an acyclic module graph; `build-module` compiles it into an ordinary Python wheel and repository publication index. A compiled graph with unfilled public ports is a module constructor. A graph without public ports is a zero-argument module factory. Both use the existing publication, synthesis, assembly locking, and offline environment commands.

In 0.4.0, graph exports with typed interfaces must also preserve parameter ownership modes, nominal value/result types, and effect bounds. Local type aliases may differ, but their identities must agree. A [checked `.mfl` program](checked-language.md) can be selected as an ordinary graph node; the new language checks operation-level resource usage inside the composition.

This implements **nonrecursive mixin linking through explicit module ports**: partial composition, reuse of partially linked modules, export merging through explicit projection, renaming, and binding operations to selected dependency modules. Recursive linking and automatic rebinding of ordinary Python globals are not supported.

## Author a graph in TOML

See the executable [open Mari retrieval graph](../examples/modules/retrieval.toml) and [complete knowledge graph](../examples/modules/knowledge.toml). This small graph demonstrates the grammar:

```toml
schema_version = 1

[family]
name = "knowledge"
version = "1.0.0"
description = "Composable knowledge retrieval and execution modules"

[publisher]
name = "integrator"

[module]
id = "retrieval"
version = "1.0.0"
provides = { id = "knowledge.retrieval", version = "1" }
capabilities = ["diversified-retrieval"]

[ports.embedding]
requires = { id = "knowledge.embedding", version = "1" }

[ports.data]
requires = { id = "knowledge.documents", version = "1" }

[nodes.ranker.select]
family = "knowledge"
member = "mari.mmr"

[nodes.retrieval.select]
family = "knowledge"
member = "adapters.retrieval"

[links]
"retrieval.embedding" = "embedding"
"retrieval.data" = "data"
"retrieval.ranker" = "ranker"

[exports]
search = "retrieval.search"

[indices]
Space = "embedding.Space"
```

Family headers follow the existing immutable-family rules. The publisher owns this contribution, not all the selected dependencies. `module.id` is a local Python identifier; publication qualifies it as `integrator.retrieval`. Ports and node names are distinct Python identifiers without leading underscores. Member versions use the existing repository's PEP 440 rules.

A node selector can specify an exact member as above or `requires = {id = "...", version = "..."}` to select by interface. Optional `version` is a PEP 440 specifier; `selection = "latest"` retains the latest version of each distinct member, not an arbitrary winning publisher. Selectors use the same grammar as authored assemblies.

Each required slot of each selected node needs exactly one entry in `[links]`. A provider is a named node or public port. Contract mismatches, unused nodes/ports, missing slots, unknown fields, and initialization cycles fail resolution. There is no implicit first-provider or last-writer-wins rule. Selecting a constructor with additional requirements means wiring those requirements or exposing additional public ports.

`[exports]` explicitly merges, renames, and projects operations/types from different nodes into the result interface. Its keys must exactly match the published public interface. Callable declarations currently require identical serialized call shapes, even when a more permissive Python function could accept the calls. Use an explicit adapter for different contracts. Private nodes remain part of the dependency closure; hiding an export does not erase its effects or initialization.

## Resolve, build, and publish

```bash
.venv/bin/mf resolve-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository --out .mf/retrieval-choices.json
.venv/bin/mf build-module examples/modules/retrieval.toml \
  --registry .mf/knowledge/repository --out .mf/retrieval-build
.venv/bin/mf publish .mf/retrieval-build/index.json \
  --registry .mf/knowledge/repository
```

These commands assume the referenced interfaces and component providers have been published; the real example below does that preparation. Local and HTTP repositories share this path, including the existing `--token-env` and `--cache` options. Source authoring is TOML only.

Resolution reports `unique`, `ambiguous`, `unsatisfied`, or `incomplete`, with bounded diagnostics. Bounds are `--max-candidates` per node (default 100), `--max-states` (10,000), and `--max-solutions` (16). A limited search cannot certify uniqueness. `build-module --choice N` explicitly chooses an alternative from the current resolution; builds resolve afresh, so inspect again if the repository has changed. The produced wheel and subsequent program lock pin the actual selection.

The builder fetches selected wheel bytes only after resolution and verifies their hashes. It reuses exact upstream artifacts, preserves their licenses, and rejects conflicting distribution versions in one closure. It does not execute selected Python during resolution/building.

Build output contains:

| File | Purpose |
| --- | --- |
| `index.json` and wheels | Normal repository publication, with exact selected artifact dependencies. |
| `generated.py` | Reviewable fixed imports and topologically ordered Python calls. |
| `contract.pyi` | Provider-independent Protocol stubs for the public result and required ports. |
| `work-contract.json` | Only the relevant public/port interfaces and semantic index requirements for a contributor. |
| `module.lock.json` | Build provenance: selected member locks, interfaces, graph identity, and wheel hash. |

`module.lock.json` is a provenance record, not an input to `lock-assembly` or an automatic rebuild command. Publish the compiled wheel, then use an assembly/program lock for replay. Installed wheels also include the generated `.pyi` beside their Python module. Stubs express call shapes with `Any` values; they do not prove return types or behavioral laws.

## Shared instances, types, and semantic indices

A named node is initialized once per generated factory invocation. Wiring two slots to that node supplies the exact same module view. Two nodes selecting the same factory initialize separately. Separate invocations create fresh factory instances; imported classes/functions retain normal Python module identity and a provider can still implement its own global state.

```toml
[constraints]
same_instance = [["retrieval.data", "harness.data"]]
same_type = [["parser.Document", "data.Document"]]
same_index = [["embedding.Space", "index.Space"]]
```

`same_instance` compares the link targets of two consumer slots at build time; both must name the same node or port. `same_type` refers to exported nominal Python types. The builder checks declarations and known identities; the generated runtime checks actual type-object identity before exposing the result. Existing constructor `sharing` declarations are also checked before invoking their factory. A global graph type constraint may fail after other factories have run; Python effects are not rolled back.

Semantic indices are opaque, declared strings. An embedding implementation can publish:

```toml
# Fields on a family member contribution
index_exports = { Space = "encoder-revision:preprocessing:metric:dimensions" }
```

A wrapper can propagate the identity from a required slot:

```toml
index_exports = { Space = { from = "base.Space" } }
index_requires = { base = { Space = "encoder-revision:preprocessing:metric:dimensions" } }
index_sharing = [["query.Space", "index.Space"]]
```

These fields are independent examples; paths must name slots actually declared by that member. A graph's `[indices]` projects indices from its nodes/ports. The builder propagates concrete identities and open equalities, rejects known contradictions, and records remaining requirements on its published constructor. Ordinary synthesis and assembly verification enforce those requirements when closing the graph. Direct invocation of generated Python also requires sealed input modules carrying the indices; `Signature.seal(..., indices={...})` supplies explicit declarations.

Matching strings establish agreement on declared identities, not that a provider actually uses the stated embedding model. Equal vector dimensions alone are insufficient. Persisted indexes still need an explicit migration when their declared space changes.

## Mixins and initialization

A cached embedding fragment requires `base: EMBEDDING` and exports another `embed`. A retrieval fragment can bind to that enriched module. Its code receives the final dependency object through an explicit parameter; its calls observe the selected enrichment. The generated code performs those bindings, so application code does not repeat the wiring.

Order is observable. `increment(double(base))` and `double(increment(base))` produce different results while satisfying the same interface. The graph's edges and artifact bytes record that order. A wrapper needs a distinct underlying port such as `base`; wiring it to its own result is an initialization cycle and is rejected.

Before factories run, the small runtime helper checks input ports and prepares all raw module interfaces/factory call shapes. Generated Python then makes fixed calls in the build's topological order. It performs no repository discovery, provider search, or source compilation. Arbitrary Python import effects remain possible; binding visibility is atomic, effects are not transactional.

Runtime resource lifecycle is explicit in ordinary contracts. The real example exports `close`; it does not automatically infer cleanup, resource ownership, transactions, or async startup. Recursive callable linking, delayed ports, and general lifecycle elaboration remain future work.

## Run the real candidate system

```bash
.venv/bin/python examples/knowledge_system.py --work-dir .mf/knowledge
```

Use an empty directory. The script publishes six interfaces, Mari's unchanged MMR algorithm, and small adapters. It compiles and publishes the open retrieval module, then links that published module into the closed knowledge system. It synthesizes a goal selecting the result, locks a complete wheel environment, installs it, and executes two queries. The expected first hits are `dogs` and `python`.

The selected implementations are Mari's `maximal_marginal_relevance`, scikit-learn 1.8.0 `HashingVectorizer`, `functools.lru_cache`, SQLite, and `ThreadPoolExecutor`. Dependency wheels are downloaded during environment locking; execution uses the resulting offline environment. HashingVectorizer supplies lexical feature vectors, not neural semantic embeddings. The executor is a bounded execution harness, not an LLM agent. See [candidate provenance and limits](../adaptations/knowledge/README.md).

To prepare a wheelhouse and keep environment resolution offline too:

```bash
.venv/bin/pip download --only-binary=:all: scikit-learn==1.8.0 "packaging>=24.0" \
  -d .mf/knowledge-wheels
.venv/bin/python examples/knowledge_system.py --work-dir .mf/knowledge-offline \
  --wheelhouse .mf/knowledge-wheels
```

For a model-backed agent system, publish the real model and harness implementations under explicit contracts and select them as graph nodes. Credentials remain runtime inputs. This release supplies the composition mechanism; it does not ship a universal LLM contract or claim those integrations were tested.

## Search and scale boundaries

Graph resolution selects providers for authored nodes; it does not invent arbitrary graph topology. Once published, an open graph participates in the existing backward-chaining synthesizer, which can recursively fill its remaining software ports. Nested graph publication provides reusable composition units without expanding the entire ecosystem into each worker's context.

Candidate enumeration is bounded and uses interface queries, but still enumerates assignments for the authored graph. There is no million-provider solver benchmark, general recursive module calculus, automatic adapter generation, or agent orchestration service. Capabilities are explicit claims of the compiled module; semantic preservation of those claims is not proved. Existing goal synthesis still aggregates capability labels and records residual obligations. A graph policy checks its selected internal effects; a closing goal's policy must also constrain providers supplied to its open ports.
