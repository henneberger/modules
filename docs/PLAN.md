# A module build system for existing Python: implementation plan

This document preserves the original research and implementation sequence. Its sequence is historical, not a list of current missing features. Current operation is described in [the build guide](build-system.md), [repository service](repository.md), and [goal-directed synthesis](synthesis.md); current verification is recorded in [final validation](final-validation.json).

## Objective

Build a module system whose main authoring workflow starts with ordinary Python source and a small TOML build declaration. The build system discovers selected definitions, resolves their implementation dependencies, checks explicit module interfaces, and produces independently publishable units. A large project should be able to accept thousands of algorithm contributions without maintaining a second, equally large hand-written inventory of those algorithms.

Reusable type libraries, signatures, named module parameters, and module-producing modules are central language concepts. The repository distributes the resulting artifacts and supports discovery and exact selection. Agent interfaces make this graph practical to explore and compose; they do not replace a precise build model. Mari Kit is the first migration candidate, copied into this workspace. An independent payment family is a controlled test of type sharing and composition, rather than a template that every existing algorithm must be rewritten to follow.

The project is named **Module Families** (`module_families`, CLI `mf`). The implementation builds Python wheels, supports local and authenticated HTTP repositories, and resolves or synthesizes checked assemblies. Distributed operation at internet scale remains an evaluation and engineering goal.

## Research and semantic foundation

The primary design artifact is [A module build system for composable Python families](RESEARCH.md). The [language review](research-theory.md), [distribution review](research-distribution.md), [critical review](research-critique.md), and [Mari audit](mari-audit.md) distinguish established results, proposed semantics, implemented checks, and remaining obligations.

ML signatures, functors, and type sharing supply the essential composition vocabulary. Backpack and Racket units show ways to add interface-based composition around an existing language. Contemporary Mω, ZipML, and Persimmon clarify transparency, abstraction, and coherent family extension. Unison and Nix inform fine-grained identity and dependency closures; Python distribution standards provide an interoperable artifact boundary.

A domain family, an ML module family, and technical family polymorphism are different concepts. We adopt explicit module parameters and shared identities. Runtime Python call-shape and nominal-type checks do not inherit ML's static soundness or abstraction theorems. The thesis that agents make much finer software granularity economical is an empirical hypothesis to test.

## Authoring and build model

The source of truth is existing Python plus compact declarations of intent. A TOML file identifies the source package, discovery boundaries, shared family context, and exceptional metadata. Contributor fragments add local declarations near the code they describe. Generated inventory records every discovered member and its provenance; authors do not maintain that inventory by hand.

The frontend lowers these inputs to the same normalized catalog used by compilation, publication, and discovery. Authored families require TOML and an explicit publisher. JSON is used for generated indexes, locks, and machine responses. The size of such a build output is a separate concern from the size of the authoring surface.

```toml
schema_version = 1

[publisher]
name = "alice"

[family]
name = "algorithms"
version = "0.1.0"
description = "Reusable algorithms from our existing Python project."

[source]
root = "../project/src"
package = "our_algorithms"

[discovery]
public = true
include = ["**/*.py"]

[contributions]
include = ["members/**/*.toml"]
```

A contributor can add `members/ranking.toml` without editing the family root:

```toml
[[members]]
id = "ranking.rank_items"
kind = "algorithm"
solves = ["Order scored items with a stable tie policy."]
```

The member refers to an existing definition and is published as `alice.ranking.rank_items`. Routine source facts are derived. An explicit `symbol` resolves cases where the intended binding cannot be inferred unambiguously. Optional `provides`, named `requires`, and `sharing` declarations express module interfaces; discovery alone does not invent them. Builds may register several disjoint package roots and explicit module scopes. Independently authenticated publishers may also contribute to the same family from separate builds. Distributed federation remains a separate problem.

```text
Python source + family TOML + contributor fragments
    → static inventory and declaration diagnostics
    → normalized module graph and explicit adaptations
    → shared implementation cells and member facades
    → deterministic artifacts and generated repository index
    → exact selection, checked composition, and import
```

Discovery selects publication roots; dependency analysis determines what those roots need. A helper can belong to a selected algorithm's implementation closure without becoming a separately advertised algorithm. Type libraries can be explicit public roots while remaining shared dependencies of many algorithms. A function that already has a usable interface needs no new wrapper merely to become publishable.

An explicit adapter or module factory is appropriate when an author wants to introduce named module dependencies, translate a representation, hide exports, initialize state, or enrich behavior. That adaptation is ordinary reviewable Python with its own identity and obligations. The build system must not silently invent business semantics, claim interchangeable interfaces from similar names, or rewrite reflection-dependent code speculatively.

## Design decisions

1. **Keep declarations proportional to intent.** Discover ordinary public definitions statically. Use common defaults and narrowly scoped overrides for domain guidance, versioning, contracts, effects, and publication exceptions. Do not duplicate full source docstrings and annotations in author-maintained TOML.
2. **Let contributions remain local.** Resolve explicitly included contributor fragments deterministically. Duplicate explicit member identities, conflicting declarations, and ambiguous names must produce actionable diagnostics. A project-wide configuration supplies shared policy; each contributor should usually edit their own code and fragment.
3. **Preserve source behavior conservatively.** Parse without importing candidate code. Build the definition dependency graph and combine strongly connected components. Preserve supported decorators, defaults, assignments, annotations, and imports. Reject unsupported extraction with a location and reason; explicit adaptation is preferable to a successful build that changes behavior.
4. **Share reusable implementation and type cells.** Generate shared modules under `mf_cells.c_<digest>` and small member facades under `mf_members.m_<digest>`. Members using one supporting class load the same class object in the checked interpreter. This mechanism supports independent publication without duplicating every supporting definition.
5. **Separate family context from compatibility.** A family can contain many signatures: charging, refunds, ledgers, and types, for example. Substitution requires the appropriate contract and sharing constraints; a family label or matching description does not establish it.
6. **Make module-producing modules ordinary build targets.** Named parameters express interface dependencies. Explicit signatures check required exports and accepted call shapes. Shared nominal types are checked by actual Python object identity. Constructors can add state and behavior, and constructor order is part of the assembly.
7. **Keep identities distinct.** Logical names, signature versions, source provenance, immutable artifact digests, shared type identities, and runtime instances serve different purposes. An identical wheel is not proof of behavioral equivalence; a fresh instance token is not a fresh ML abstract type.
8. **Build interoperable, deterministic artifacts.** Member and cell wheels carry exact internal dependencies, ordinary external requirements, and licensing files. Importing a selected facade does not import the original package initializer. A selected closure can be installed with existing Python tools.
9. **Generate machine inventory and retrieve bounded slices.** The generated index may be large, but authors and agents should not need the whole index in their working context. Indexed discovery, dependency explanations, and build diagnostics expose relevant subsets. Metadata discovery and planning never import candidate code.
10. **Bind explicitly and verify before execution.** Publication is immutable. Locks identify exact internal wheel closures. The atomic import API validates the complete selected graph before loading exports and returns only a completed linked bundle. In-process Python effects and module caching are not rolled back on failure.

## Component boundaries

| Layer | Input | Output and responsibility |
| --- | --- | --- |
| Authoring frontend | Python, compact TOML, explicit fragments and overrides | Validated build intent; deterministic discovery and conflict diagnostics |
| Static inventory | Declared discovery scope and Python syntax | Derived member descriptions, locations, signatures, and source provenance |
| Module graph compiler | Selected roots, implementation edges, explicit adaptations | Shared cells, member facades, exact dependency relationships |
| Artifact builder | Compiled graph and distribution metadata | Reproducible wheels, generated index, required licensing material |
| Repository | Build outputs and publisher principals | Immutable interfaces and member publications, authenticated HTTP transport, indexed discovery, exact closure locks |
| Planner and linker | Goals or declared expressions, repository candidates, published signatures | Bounded synthesized or resolved expressions, rejected candidates, residual obligations, checked modules |
| Runtime | Verified program and environment locks | Complete import bundle or execution through a private wheel environment; documented effect boundaries |

The normalized catalog is an internal interchange boundary, not a demand that maintainers author one full card per definition. Artifact indexes carry the fields needed for exact installation and discovery. Member locks retain selected internal closures. Separate assembly locks commit expressions and published interfaces; environment locks resolve wheel dependencies and fingerprint the interpreter for offline replay.

## Original implementation sequence

1. **Establish the research model and migration evidence.** Finish the cited review and distinguish formal specification from conjectures and tested properties. Preserve an auditable copy of Mari's source, tests, documentation, licenses, and provenance. Record extraction limits before broadening supported Python constructs.
2. **Make the compact build frontend the normal entry point.** Add TOML family declarations, static discovery, reusable defaults, explicit overrides, and distributed contributor fragments. Normalize them into the existing catalog model. Migrate checked-in candidate configuration away from generated full inventories; generate those inventories under build output instead.
3. **Expose an inspectable build plan.** Show selected roots, required helper/type cells, external requirements, unsupported constructs, and the reason each dependency is included. Report discovery and declaration conflicts before artifact production. Existing algorithms should generally require source selection and metadata, not rewritten implementations.
4. **Compile and publish independently.** Use the definition graph and deterministic wheel builder for every supported selected member. Validate recursion, closure precision, shared class identity, licensing, external dependencies, and reproducibility. Preserve immutable publication and import-free repository discovery.
5. **Connect module semantics to those build products.** Compile ordinary functions, reusable types, closed module factories, and open constructors through the same path. Bind named requirements using runtime signatures and nominal sharing. Interpret concrete module-expression trees over verified loaded exports; make unresolved requirements and unsupported representations explicit.
6. **Demonstrate composition and atomic activation.** Publish the independent payment family through the generic build path. Compare `Idempotency(Ledger(Retry(P)))` with `Ledger(Idempotency(Retry(P)))`: both suppress repeated charge effects in the local fixture, but they record different numbers of receipts. Use the example to state precise assumptions, rather than generalize it to external payment guarantees.
7. **Validate the existing-code migration and contribution workflow.** Build Mari, run representative algorithms from installed artifacts outside the source tree, compare behavior with the source copy, and run relevant upstream semantic tests. Add independent contributor fragments in a synthetic large project to test bounded authoring effort, deterministic merges, dependency precision, and actionable failure messages.
8. **Measure and refine.** Record source/declaration sizes, generated inventory sizes, build time, artifact count, closure bytes, incremental invalidation, discovery cost, and composition outcomes. Only then prioritize larger distributed storage, namespace authority, attestations, revocation, full environment locks, and stronger static or behavioral checks.

The sequence above records the plan that guided development. Current code additionally includes mandatory publisher namespaces, multi-source compilation, coarse persistent analysis caching, reusable published interfaces, authenticated remote publication, recursive capability synthesis, and wheel-environment locking. The compact frontend and contribution workflow still require independent scale and agent-quality evaluation; implemented examples do not establish those claims.

## Acceptance criteria

- The original Mari project remains unchanged; its copy and provenance are present here.
- Research supports the semantics with primary references, includes recent work, and avoids claims of exhaustive coverage or inherited static soundness.
- A compact TOML declaration can expose existing Python algorithms without an author maintaining a generated per-definition JSON inventory.
- Independent contributor fragments compose deterministically; conflicting declarations fail clearly rather than silently overriding one another.
- Static discovery derives routine documentation and source facts without executing candidate code. Semantic claims remain explicit author assertions or separately identified evidence.
- Each supported selected public algorithm has an independently installable artifact. A small pure function does not pull in the original monolith or unrelated numerical libraries.
- Shared supporting types retain object identity across separately built members. Contract and sharing checks reject incompatible bindings before an enclosing factory uses them.
- Existing behavior is preserved for supported extraction cases; unsupported cases receive useful diagnostics and explicit adaptation paths.
- Publication rejects content replacement, damaged artifacts, and invalid dependency graphs. Import returns a completed checked bundle or raises, with no rollback claim for arbitrary Python effects.
- Measurements separate authoring burden, generated inventory size, artifact granularity, runtime composition, and actual scale. No benchmark on two families is described as proof of megascale operation.


## Implemented agent-composition increment

The 0.7.0 increment connects separately checked associated-type operation bodies,
bounded worker context, staged code contributions, independent evaluation,
Ed25519/HMAC observations, prerequisite campaigns, evidence-aware synthesis, and
read federation. An actual coding agent composed the existing knowledge providers
in TOML and passed the original system contract. See the README for the executable
workflow and the validation reports for exact workload sizes. This closes the
local contribution-to-system loop; neither a successful local task nor a million
queue records establishes operation with millions of concurrent coding agents.
