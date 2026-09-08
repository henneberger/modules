# Build-time module composition for independently developed systems

Status: architectural proposal, 2026-09-08, partially implemented in 0.3.0. The [module build guide](module-build.md) is the authoritative shipped grammar and behavior: nonrecursive open graphs, fixed Python wiring, shared nodes, semantic indices, and contract stubs now work. The historical syntax below remains illustrative and differs from the shipped grammar. Recursive linking, general lifecycle elaboration, stronger capability proofs, and autonomous worker orchestration remain proposed.

## The unit of independent work

A contributor should be able to receive a small contract, implement it in normal Python, and publish a component without loading the surrounding application's source or choosing its concrete dependencies. Another agent should be able to assemble those components into a system by satisfying their requirements. A partially assembled subsystem should itself be publishable, preserving its remaining requirements for a later builder.

The build language describes the structure of a program: imports, exports, identities, constraints, and composition. Python implements its operations. The compiler elaborates the selected structure into ordinary Python modules and wheels. A deployed program needs fixed initialization code and application execution; it does not need repository search or an agent making dependency choices at import time.

Composition still requires APIs: contracts describe operations. The change is that the *choice and wiring* of implementations belongs to the module build, rather than repeated handwritten application setup. Credentials, live database connections, cancellation, and per-request data remain runtime inputs. A build can select a database driver and declare its resource requirements without opening the production database.

## Research basis

Backpack is a particularly close precedent: it adds interface-based separate development and mixin-style linking above an existing language, with elaboration into ordinary language modules. Its results apply to its Haskell design, not automatically to Python. [Backpack, POPL 2014](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf).

MixML supplies the foundation for treating defined and undefined components together and supporting recursive linking. This motivates open module fragments rather than treating every extension as class inheritance. [Mixin' Up the ML Module System](https://research.google/pubs/mixin-up-the-ml-module-system/).

Racket units demonstrate partial linking concretely: imports can be connected to exports before invocation, and the resulting compound unit retains unresolved imports. That is the behavior wanted for an independently published, unfinished subsystem. [Racket units](https://docs.racket-lang.org/guide/units.html).

Persimmon studies coherent extension of related nested components through family polymorphism. It motivates preserving relationships when a subsystem is extended; copying metadata or replacing a Python class alone does not implement its guarantees. [Persimmon, OOPSLA 2024](https://cs.uwaterloo.ca/~yizhou/papers/persimmon-oopsla2024.pdf).

The design below is our engineering proposal informed by those mechanisms. See the [theory review](research-theory.md) for the broader lineage and its limits.

## A small module calculus

Represent a fragment as `M = (R, E, C, I, B)`: required ports, exported components, constraints, initialization dependencies, and a Python implementation/build recipe. Ports have scoped names and exact contract references. Constraints include type identity, semantic indices, instance sharing, and declared behavioral obligations.

The useful operations are:

| Operation | Meaning |
| --- | --- |
| Require / provide | State assumptions and implemented components independently. |
| Apply | Supply arguments to an existing module constructor. |
| Link | Connect exported ports to required ports; keep unfilled ports explicit. |
| Rename | Map ports into a common namespace without changing their meaning. |
| Project / seal | Select a public interface while retaining private dependency obligations. |
| Refine | Add explicit equalities or stronger obligations to a contract. |
| Instantiate | Choose whether occurrences share a runtime instance or create fresh ones. |
| Close | Require all software ports resolved before producing an executable program. |

For a checked wiring map `w`, `link(M, N, w)` unions qualified exports, removes the requirements discharged by `w`, and combines constraints and initialization edges. Export conflicts fail unless explicitly renamed or selected. Wiring uses scoped port identity and compatible contracts; equal short names are insufficient. Constraints must remain consistent. An open result can be published; closing it fails while software requirements remain unresolved. Declared runtime configuration slots may remain, since they are part of the program's launch interface.

Do not define linking as unrestricted dictionary merging. Do not assume link order is irrelevant when initialization or effects differ. A future formal specification should state which restricted pure links are associative and establish preservation of port satisfaction and identity constraints. Those are proposed proof obligations, not existing theorems about this codebase.

## Mari as a candidate system

The copied Mari source already exposes relevant boundaries. Its [retrieval research module](../projects/mari-kit/src/mark_kit/retrieval/research.py) consumes externally produced embeddings and accepts callbacks for clustering and summarization. Its [store protocols](../projects/mari-kit/src/mark_kit/platform/stores.py) describe scoped reads, revision commits, and history. These are candidate adaptation points. An agent harness is a proposed integration dependency, not an existing published Mari harness module.

An illustrative knowledge-system contract would decompose into:

| Port | Contributor needs to know | Composition obligations |
| --- | --- | --- |
| `types` | Document, scope, revision, message, and result contracts | Shared nominal types where objects cross boundaries. |
| `model` | Generation operations and failure/cancellation semantics | Required structured output or tool-call support, if used. |
| `embedding` | Query/document embedding operations | One agreed space identity, preprocessing, metric, and dimensions. |
| `data` | Scoped storage and revision operations | Required consistency, scope isolation, and instance lifetime. |
| `index` | Vector lookup and update operations | Same embedding space as its query producer; compatible index generation. |
| `retrieval` | Retrieval inputs/outputs and dependency ports | Selected Mari algorithms plus reviewed boundary adapters. |
| `harness` | Model/tool invocation and execution context | Compatible model, tools, cancellation, and execution budget. |

Use a common model contract with explicit refinements such as structured output or tool invocation. Avoid a universal contract that forces every model provider to support every operation. The same applies to data layers: a read-only store and a transactional revision store should have distinct requirements.

Embedding compatibility is a crucial example of why Python types alone are insufficient. Two arrays with equal dimensions can represent incompatible embedding spaces. Introduce a declared semantic index, `Space`, identifying the model revision and embedding configuration; require `query.Space = index.Space`. Keep separate laws for asymmetric query/document encoders. Changing that space invalidates the index compatibility claim and requires an explicit data migration/reindexing operation; rebuilding Python wheels does not rebuild stored vectors.

Similarly, the same `Revision` class does not imply two stores share a transaction or tenant. Distinguish type equality, semantic-index equality, and runtime-instance equality. Artifact reuse must not silently become a global mutable singleton.

## Proposed TOML build language

Keep TOML as the initial DSL. Give it a typed intermediate representation with named nodes and edges, so a future textual DSL or agent editor can target the same semantics. The following is **illustrative future syntax**, not accepted input for `mf` today; the contract IDs are examples, not claims of published providers.

```toml
module_language = "proposal-1"

[module]
name = "knowledge-system"
provides = "knowledge.system@1"

[ports.model]
requires = "models.generator@1"
capabilities = ["structured-output"]

[ports.embedding]
requires = "vectors.embedder@1"

[ports.data]
requires = "storage.revisions@1"
capabilities = ["scope-isolation", "compare-and-swap"]

[fragments.retrieval]
select = { requires = "knowledge.retriever@1" }

[fragments.index]
select = { requires = "vectors.index@1" }

[fragments.harness]
select = { requires = "agents.harness@1" }

[link]
"index.embedding" = "embedding"
"retrieval.embedding" = "embedding"
"retrieval.index" = "index"
"retrieval.data" = "data"
"harness.model" = "model"
"harness.knowledge" = "retrieval"

[constraints]
same_index = [["embedding.Space", "index.Space"]]
same_type = [["retrieval.Document", "data.Document"]]

[exports]
answer = "harness.answer"
retrieve = "retrieval.retrieve"
```

This describes an *open* module: model, embedding, and data ports remain available to downstream builders. Other requirements of selected fragments remain explicit too. A final goal fills them through constrained selection or explicit bindings, checks the resulting graph, and locks it. Publish this subsystem without dictating every provider in its ecosystem. References such as `index.Space` denote signature-level declarations and must be checked without importing provider code; assertions about runtime objects still require link-time checks.

The DSL should remain a finite structural language. Loops and arbitrary Python execution during selection would undermine reproducibility and bounded search. Sharing named graph nodes avoids expanding one dependency tree separately for every consumer.

## Mixins and behavioral enrichment

A module mixin is an open fragment contributing behavior while requiring other behavior. It can export `answer` while requiring `retrieve` and `generate`, or export a cached embedding operation while requiring an underlying embedding provider and a cache. Partial links can be reused before all of those requirements are filled.

Late binding matters when one fragment's `answer` must call the *final* composed `retrieve`. Python functions retain their defining globals, so merging export maps will not accomplish this. Support explicit dependency objects or parameters in Python implementations, with generated wiring. For existing packages, use a narrow reviewed adapter or an explicit, recorded source transformation. Do not silently patch `sys.modules` or rewrite arbitrary globals.

Decorating an operation needs a distinct underlying port: a retry fragment can require `base.generate` and export `generate`. Otherwise resolving its requirement back to its own export creates accidental recursion. Composition order is explicit: tracing outside retry records a logical call; tracing inside retry can record each attempt. Both can satisfy the same call signature while providing different behavior.

Capability synthesis therefore needs rules about *exported behavior*, not just the union of labels anywhere in a dependency graph. A proposed rule might establish a retrying `generate` only when the wrapper consumes the selected base operation and the permitted failure/operation policy is satisfied. State preservation or loss of capabilities explicitly, and attach tests/evidence. Such rules remain claims unless supported by a stronger proof system. The current synthesizer's label union does not provide this analysis.

Begin with acyclic initialization. Later distinguish recursive callable references, which may be delayed until all bindings exist, from eager initialization dependencies, which may not cycle. Normal Python decorators, class bases, defaults, and module-level expressions can all demand values during import. Supporting recursive links requires analyzing or constraining those demands, not merely finding strongly connected components.

## Build products and runtime boundary

The proposed pipeline is: parse contracts and fragments → normalize a port graph → select providers and adapters → solve identities and obligations → validate initialization → generate fixed Python wiring → build wheels → lock and validate the closed program.

Generate contract `.pyi` stubs and bounded conformance fixtures so an implementation can be checked before its concrete providers exist. Optional Python static checking supplies useful evidence, but `Any`, reflection, and dynamic behavior prevent a blanket ML-style soundness claim. Source extraction, metadata checks, Python type checks, behavioral tests, and runtime checks must be reported separately.

The output should include the selected artifact closure, graph/port bindings, public Python facade, initialization plan, remaining launch parameters, and an evidence report. A small generated bootstrap binds resources and creates instances; provider selection has already finished. Async resource acquisition, shutdown, and ownership need explicit lifecycle contracts, including what happens after partial startup failure. Secrets are launch inputs, not lock contents.

The compiler identity should depend on fragment content, contract digests, dependency identities, wiring, relevant configuration, and toolchain. Runtime instance identity is separate. Replacing an embedding implementation invalidates its dependent composition/evidence without rebuilding unrelated algorithms. Public contract stability can allow reuse of a separately checked implementation even when the final bound artifact changes. Fine-grained invalidation is a target beyond today's coarse analysis cache.

## Thousands of independent agent contributions

Publish a *work contract* containing the required signature slice, dependency assumptions, semantic indices, allowed effects, conformance fixtures, acceptance criteria, and source adaptation rules. A worker needs that slice and relevant implementation source, not the full system graph. Contract tests must have independent expectations; having a contributor generate both implementation and all its expected answers is weak evidence.

A coordinating agent decomposes an open goal into work contracts. Workers publish candidates under their own identities. Integration searches for compatible candidates, validates combinations, and returns a small conflict explanation when assumptions disagree. This is a proposed development workflow, not a currently implemented subagent orchestration service.

Local conformance is necessary but insufficient. Integration must still check shared resources, embedding spaces, lifecycle, and end-to-end behavior. New contracts need versioned agreement; independent workers cannot silently redefine the meaning of an existing port. A family remains an open discovery ecosystem, while a release is an exact selected composition.

Scale discovery through signature/index queries, lazy metadata, reusable open subassemblies, and incremental constraint solving. Cache failed and successful subproblems under the full constraint context. Do not enumerate every possible combination of thousands of providers, or send a full family inventory to each agent. Search remains bounded and reports ambiguity or incompleteness rather than guessing a unique program.

## Implementation sequence and acceptance

1. Specify the port-graph IR and TOML schema. Implement nonrecursive open linking, renaming, projection, and conflict diagnostics. Reuse existing constructors as a lowering target.
2. Add declared semantic indices and explicit instance/lifecycle constraints alongside nominal type sharing. Generate Python stubs and report which obligations remain unchecked.
3. Emit fixed Python wiring and lock graph structure, initialization, and instance choices. Verify that two differently configured instances coexist without global module rebinding.
4. Extend synthesis from constructor trees to shared open graphs and explicit capability propagation rules. Publish and later close partially linked subsystems.
5. Adapt a real Mari retrieval boundary and real embedding/storage/harness providers with preserved provenance. Keep necessary adapters small; do not replace upstream algorithms with demonstration implementations.
6. Add recursive late binding only after initialization semantics and failure tests are specified. Investigate coherent nested family extension separately from simple provider substitution.

Acceptance requires independently building a retrieval contributor against contracts with no installed concrete harness, embedding provider, or database; publishing it into a family it does not own; closing it with real providers; executing the generated Python facade offline where the selected providers permit offline operation; rejecting mismatched embedding spaces and scope/revision contracts; observing explicitly different mixin orders; and replaying an old lock after new contributions appear. External services still require their declared runtime connectivity. Include an integration test showing that replacing one port does not require editing consumer Python.

This establishes the broader architecture. The new [knowledge-system example](../examples/knowledge_system.py) exercises its nonrecursive linker with Mari, scikit-learn, SQLite, and a Python executor. It does not establish all the stronger goals in this proposal.
