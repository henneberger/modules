# A module build system for composable Python families

For the current executable workflow, see [goal-directed module synthesis](synthesis.md), [the build system](build-system.md), and [the authenticated repository](repository.md). This proposal distinguishes those implemented mechanisms from stronger typing hypotheses and future research; current integration evidence is recorded in [final validation](final-validation.json).

## Research thesis

The central object is a build system that turns existing Python into independently reusable modules. An author supplies ordinary source and a compact declaration of publication intent. The build system discovers definitions, resolves implementation dependencies, preserves shared type identities, and produces artifacts with explicit module interfaces. Existing algorithms should become build targets without a parallel hand-written inventory or a required rewrite into a new programming style.

Those modules can include reusable type libraries and module constructors that transform other modules. A repository distributes them, and an agent can assemble a coherent selection for a task. The proposed system therefore combines a build frontend over Python, a language of module interfaces and composition, a repository preserving identities and evidence, and an agent interaction model. These layers have different responsibilities. A successful build is not proof of behavioral compatibility, a useful search result is not a valid composition, and a valid composition is not evidence of application success.

The central hypothesis is that machine-assisted selection and maintenance change the economically useful granularity of software. Humans may find thousands of small components burdensome to manage. Agents could make those components practical if interfaces, dependencies, examples, and compatibility obligations are explicit and mechanically accessible. The build system should derive routine facts and let contributors declare the intent that source analysis cannot establish. Moving the burden into thousands of manually maintained metadata cards would weaken the hypothesis. This is a testable claim about coordination cost, not an assumption that model intelligence eliminates software correctness problems.

Mari is the first substantial existing-code migration experiment. A separate payment family tests reusable type libraries and behavior-enriching composition. Neither defines the general build model, and the payment constructors are not mandatory scaffolding for unrelated algorithms. The prototype is an executable research instrument; megaproject contribution workflows, distributed repository economics, and stronger language guarantees need separate evaluation.

## Intellectual basis

ML provides the essential distinction between structures, signatures, shared type identities, and functors. Harper and Pierce explicitly discuss signature families and module families. Later family-polymorphism research concerns coherent extension of related types and operations; merely placing packages in a common category does not provide those properties.[^1]

Backpack demonstrates that interface-based linking can be added above an existing language's package structure. Racket units give another relevant precedent: explicit imports and exports, independent replacement, and dynamic assembly in a language that does not require an ML-style core. Both motivate treating composition as a language-level subject, distinct from archive delivery.[^2][^3]

Contemporary work has continued to refine these foundations. Persimmon addresses nested family extension, variants, and pattern matching; Mω and ZipML refine transparency, abstraction, and signature avoidance. These results provide design constraints and a vocabulary for the new system. The prototype does not inherit their soundness proofs.[^4][^5]

Higher-order contracts are particularly relevant to Python because a dependency may be a callback or an entire module-producing function. Checking the visible parameter names of a function is weaker than monitoring the obligations between a function and its callers. A serious gradual module system needs to distinguish such contract levels rather than describe all validation as typing.[^6]

Unison, Nix, Python distribution standards, and WIT inform identity, deployment, and interoperability. The companion [theory review](research-theory.md) and [distribution review](research-distribution.md) contain the wider source inventory, including publication dates and caveats. Coverage is a focused review through September 2026, not a claim that every relevant paper has been read.

## Authoring a large module project

A project with thousands of algorithms needs a small, compositional build description. The proposed normal input is TOML describing source roots, discovery boundaries, common family context, explicit interfaces, and exceptions. Contributor fragments hold local declarations near their implementations and are included deliberately. Python source remains authoritative for definitions, docstrings, annotations, and implementation dependencies; static analysis derives those facts without importing candidate code.

The build frontend lowers this input into a normalized inventory and dependency graph. JSON is an appropriate generated representation for that graph, repository indexes, machine responses, and locks. It is not the intended authoring surface for tens of thousands of lines of repeated source facts. Generated metadata can grow with the codebase while the configuration grows mainly with semantic choices and exceptions.

```text
source + compact TOML + contributor fragments
    → discover publication roots
    → derive implementation dependencies and source facts
    → check explicit interfaces and adaptations
    → compile shared cells and member artifacts
    → generate inventory, index, and exact selected closures
```

This division creates specific obligations. Discovery must be deterministic and explain why each definition is public or excluded. Fragments must have clear path resolution and reject conflicting member identities. Derived descriptions must remain distinguishable from curated claims such as safe retry behavior. Metadata changes, code changes, and dependency changes need different invalidation rules. A useful build plan exposes these decisions before writing artifacts.

An algorithm with an adequate existing call interface should need no new wrapper. A module factory is an explicit tool for introducing dependency parameters, initializing state, or assembling multiple exports. A behavioral adapter is necessary only when an intended composition requires a real conversion or change. Such adapters should be ordinary reviewable source with contracts and evidence; the compiler cannot infer safe rounding, retry, or data-loss policies from the desire to make two modules fit.

Contributor scalability is part of the research problem. A new algorithm should normally involve its implementation, relevant tests, and a small local declaration if defaults are insufficient. It should not require editing a global list of every public definition. At the same time, compact configuration must not hide surprising publication decisions: a dry build inventory and dependency explanation remain necessary. The evaluation must measure conflict frequency, declaration size, discovery determinism, and incremental build cost alongside repository retrieval. The implementation supports several disjoint package roots, explicit module scopes, and coarse persistent analysis caching. Independent publishers can contribute to one family through an authenticated repository; distributed federation remains a further design problem.

## The build graph and repository objects

The build graph and resulting repository contain several kinds of object. Treating all of them as interchangeable packages would discard important semantics. Build declarations describe how to derive objects; the generated inventory records what a particular source snapshot and build configuration actually produced.

| Object | Contents | Identity and use |
| --- | --- | --- |
| Build declaration | Discovery scope, shared defaults, contributor fragments, explicit adaptations | Compact source-controlled authoring intent |
| Derived inventory | Discovered definitions, source facts, selected roots, compilation diagnostics | Generated view of a particular build input |
| Type component | Abstract type declarations, manifest type identities, codecs, invariants, constructors | A reusable basis for other modules' interfaces |
| Signature | Named values, types, nested modules, call contracts, law references | Defines an interface and its obligations |
| Module | Implementation of a signature, explicit required modules, declared effects | Supplies behavior with a checked boundary |
| Module constructor | Requirements and a rule producing another module | Adds, specializes, combines, or changes behavior |
| Family | Shared vocabulary, type libraries, roles, constructors, candidate members | Describes a domain of related software without requiring one installation |
| Assembly | Concrete bindings satisfying a module expression's requirements | A coherent software graph for an application |
| Artifact | Bytes and exact implementation dependencies | Can be fetched and verified independently |
| Evidence | Subject identities, specification, environment, workload, result, producer | Supports a particular claim under particular conditions |
| Catalog snapshot | A view of names, candidates, and evidence at a point in repository history | Makes selection context attributable |

A type component is a first-class reusable dependency. For example, `Money` may fix integer minor units, currency representation, and validation, while `Receipt` refers to exactly that `Money` identity. Two processors can share those types and still expose different implementations. A fraud classifier may belong to the same payment family while providing a different signature.

Families should also be able to supply parameterized type libraries. A future family could be parameterized by currency policy, storage identity, or a graph's node representation. Every dependent member must then refer to the same chosen instance of that library. This is more expressive than assigning every Python class a globally unique name; the relationships among types matter.

The prototype implements reusable manifest Python type identities and named module parameters. Parameterized abstract type families, nested further binding, and automatic adaptation of inherited code to extended variants are proposals, not implemented language features.

## A small module calculus

The following core is a design specification. It is intentionally smaller than an ML calculus and separates what a metadata checker can decide from what Python execution must establish.

Let `a` identify an immutable implementation artifact; `κ` identify a type component; `σ` identify a signature; and `ε` denote a set of declared effects. A module interface is:

```text
Σ = ⟨T, V, R, E, Q⟩

T : type label → abstract variable or manifest type identity
V : value label → call contract
R : requirement slot → required interface and selection constraints
E : declared effect set, or unknown
Q : behavioral obligations and evidence requirements
```

A useful initial expression language is:

```text
m ::= x
    | reference(a)
    | structure { exports }
    | seal(m, Σ)
    | functor (x₁ : Σ₁, …, xₙ : Σₙ) => m
    | apply(m, { x₁ = m₁, …, xₙ = mₙ })
    | let x = m₁ in m₂
```

Nested structures and functors suffice to express many enrichments without introducing a special inheritance construct. A more general language can later add recursive links, abstract type generation, and family extension. Recursion among ordinary definitions already has an implementation representation through strongly connected cells; that does not establish semantics for arbitrary recursive module initialization.

The executable expression interpreter currently supports finite concrete trees of references and named applications. It preflights candidate metadata, checks runtime signatures, and evaluates each factory occurrence freshly over exports already loaded from verified locks. The `let` form above is a proposed language feature, not implemented instance sharing. Arbitrary Python factory bodies and behavioral obligations remain outside the metadata planner's proof boundary.

An environment `Γ` records known type identities, signature definitions, and previously checked module interfaces. The central judgments are:

```text
Γ ⊢ m : Σ                         m has interface Σ
Γ ⊢ Σ_actual satisfies Σ_required  the module can fill this requirement
Γ ⊢ θ satisfies R                 all named bindings meet their requirements
Γ ⊢ θ respects S                  all declared type-sharing equations hold
```

The initial `satisfies` relation is conservative. Contract identifiers and versions must match; required exports must exist; supported calls must be accepted; declared effects must fit the requested policy; and manifest type identities must satisfy explicit equalities. Additional exports can be hidden by a sealed view. Source annotations are retained as evidence for tools but do not establish a subtype relationship automatically.

Call acceptance has a direction: every call admitted by the required signature must be accepted by the provider. An implementation that introduces a required extra argument is incompatible. A keyword-capable requirement is not satisfied by a positional-only implementation. A sync requirement is not silently satisfied by an async implementation. The runtime implementation checks these distinctions without invoking the function during the check.

For real typed values, substitutability would also require suitable input/output variance and behavioral obligations. The reference checks do not prove either from arbitrary Python annotations. This boundary is central to the research: a metadata checker can reject definite incompatibilities while leaving explicit residual obligations for static tools, contract monitors, and evaluations.

## Expressive dependencies

A dependency should state the role an implementation must play. A distribution name is only one way to choose the implementation of that role.

A closed provider and an open constructor have different interfaces. A constructor requiring a processor and producing a processor has interface `Π(processor : ChargeService).ChargeService`; its result signature alone does not make it an available processor. A zero-argument module factory can initialize a closed provider, but a constructor with unfilled slots requires an explicit application. Otherwise a solver could incorrectly select `Retry` to satisfy its own missing processor.

Consider a checkout constructor:

```text
Checkout requires
  types     : PaymentTypes
  processor : ChargeService
  ledger    : Ledger

sharing
  processor.Money   = types.Money
  processor.Receipt = types.Receipt
  ledger.Receipt    = types.Receipt

policy
  processor declares effects within {local-state}
  processor implements charge-service version 1
```

The repository can supply several candidates for `processor`. The agent may prefer one after inspecting its cost model or benchmark evidence. It cannot satisfy the requirement with a refund function merely because both belong to the payment family. Nor may it silently select a processor whose receipt belongs to a different nominal family.

Four dependency relations must remain separate:

1. **Implementation dependencies** say which exact artifacts are required to execute a selected implementation.
2. **Interface dependencies** describe unfilled module slots and their candidate implementations.
3. **Sharing constraints** require equalities among type components or other explicit identities across slots.
4. **Operational requirements** describe effects, resources, external environments, and behavioral obligations.

The reference repository verifies exact internal artifact closures. Runtime `Requirement` and `Functor` objects check named interface bindings and type sharing. Repository resolution selects member versions using PEP 440 constraints, and bounded synthesis recursively assembles constructors from published interfaces and declared capabilities. A separate environment stage delegates wheel dependency resolution to pip and locks the selected wheels and interpreter fingerprint. These layers do not form a general solver for mutually recursive higher-order interfaces, arbitrary Python programs, or behavioral proofs; their complexity and completeness boundaries remain explicit.

Effects also have phases. Import-time initialization, module factory application, and later calls through the produced module can have different effects. The current cards carry a coarse declaration, and constructors propagate dependency-effect declarations. A richer signature should distinguish initialization effects from latent operation effects; the current representation is not an effect calculus or an enforcement mechanism.

An agent should receive residual requirements instead of a false success. “Module interfaces match; NumPy environment is unresolved” and “two bindings remain admissible” are useful results. The repository must not turn a retrieval score or a default latest version into an unstated semantic decision.

## Composition that changes behavior

Module composition is valuable when the result has behavior that no input module supplies alone. A constructor can use the exports of its inputs, maintain new state, impose new invariants, and expose a different signature.

The payment experiment uses independently distributed components to construct:

```text
AuditedCharge = Idempotency(
    service = RecordSuccess(
        service = Retry(service = FlakyProcessor(types)),
        ledger = MemoryLedger(types)),
    types = types)
```

`Retry` turns a service that can fail transiently into one that makes a bounded number of attempts. `RecordSuccess` records a successfully returned receipt. `Idempotency` remembers completed logical requests and rejects reuse of a key for a different amount. The resulting service combines these behaviors while preserving shared `Money`, `Receipt`, and error identities.

Composition order is meaningful. `RecordSuccess(Retry(P))` can record one logical success, while an attempt-recording layer inside `Retry` can record multiple attempts. `Idempotency(Retry(P))` can suppress later repeated requests after a successful completion. Neither order alone guarantees exactly-once external payment effects after an unknown remote outcome. That stronger property requires a provider protocol, durable state, and assumptions about failures.

The executable order experiment is more specific: `Idempotency(RecordSuccess(Retry(P), L))` and `RecordSuccess(Idempotency(Retry(P)), L)` receive twenty concurrent calls with the same key. With fresh local instances, both produce one charge effect and two provider attempts, because the fixture fails once before any effect. The first composition records one receipt; the second records twenty returned receipts. The same published constructors therefore yield different observable behavior solely through composition order. These are measured properties of this fixture and workload, not inferred laws of every retry or ledger implementation.

These order-dependent differences are productive research examples. A family can publish constructors with laws and preconditions such as “records successful returned receipts,” “preserves amount representation,” or “retries only errors declared safe for this provider.” The agent composes them by those meanings and then evaluates the resulting service against the intended workload.

The same pattern applies outside payments. A retrieval index module can be enriched with authorization filtering, incremental maintenance, calibrated reranking, or provenance traces. A parser can be combined with coordinate-preserving normalization and incremental diff. A graph algorithm can be specialized to a node-type library and enriched with temporal filtering. Such examples need explicit laws: filtering after candidate selection, for instance, may behave differently from restricting the search domain before selection.

The system should make constructors ordinary publishable modules. A popular composition can itself become a named module constructor or a fully bound assembly. This creates reuse at multiple levels without forcing all future applications to start from individual functions.

## Atomic import and coherent activation

An atomic module import should expose one complete binding of a coherent dependency graph. This is a stronger and more useful goal than sequentially installing unrelated packages, but it needs an exact observation boundary.

```mermaid
flowchart LR
    A[Task and module expression] --> B[Candidate search]
    B --> C[Resolve interfaces and sharing]
    C --> D[Lock exact artifact closure]
    D --> E[Verify and stage all artifacts]
    E --> F[Load and link in a private binding map]
    F --> G[Expose one completed bundle]
```

There are three different atomicity properties:

| Property | Meaning | Reference boundary |
| --- | --- | --- |
| Coherent selection | Every named slot and shared type refers to one explicit chosen graph | Exact locks and sharing checks |
| Atomic binding visibility | Consumers receive the completed bundle, or an error; no partial bundle is returned | Private construction followed by one return |
| Transactional execution | A failed import or factory leaves no observable runtime effects | Requires an execution boundary; not supplied by in-process Python |

The reference `atomic_import` follows prepare, verify, load, link, then return. It verifies all supplied locks before executing any selected export. It stages the union of files in a content-named directory and rejects conflicting ownership. It then builds a private map and performs the requested linking before returning a bundle.

Python can still cache modules, mutate global state, or execute effects during import and factory application. If linking fails after that point, no completed bundle is returned, but those effects may remain. Locked environment execution now uses a separate worker subprocess. A persistent worker service that exposes only successful instances through handles would be a further mechanism; external effects would still require appropriate protocols. Claiming rollback for ordinary Python imports would make the design unsound at its most important boundary.

## Identities and evolution

The repository should distinguish at least five identities: a logical family name, a signature reference, a manifest type identity, an immutable artifact digest, and a runtime instance identity. An assembly adds a sixth: the identity of its exact bindings and constructor expression.

The low-level bundle's `identity` commits to its locked artifact environment and aliases. An arbitrary Python `link` callback is not included. The runtime `Functor` identity uses caller-managed logical identities and contract metadata, not a verified hash of all factory code and configuration. The implemented higher-level assembly lock supplies the stronger boundary: it persists and hashes the concrete expression, immutable constructor/provider artifacts, published interface specifications, effect policy, and residual obligations. Synthesis locks also retain their goal and search bounds. This identity still does not prove behavioral correctness.

Changing a search description must not be confused with changing an implementation. Changing a contract's semantics is not an innocuous patch to its prose. Reusing an artifact does not imply reusing a stateful client instance. Rebuilding a source file does not justify changing the identity of an unrelated declaration within it.

The definition compiler hashes selected source statements, defining bindings, compiler format, and dependency identities into shared cells. A per-member wheel points to its required cells. Two members using the same supporting class therefore load the same class object in a trusted interpreter. A changed supporting type can receive a new identity without replacing the old bytes.

Compact TOML discovery keeps routine source-file hashes and line numbers out of member cards, so an unrelated sibling edit can leave both a selected cell and its facade unchanged. Legacy or explicitly supplied cards can still contain broader provenance that changes a facade independently of its defining cell. Changing licensing files without changing a cell can also produce a new archive under the same cell distribution name/version, which immutable publication rejects. Separating executable identity, packaging/legal identity, and revisable assessment objects remains an explicit evolution task.

This gives an artifact-level model of reuse and change propagation. It is not a proof of semantic equivalence: equivalent programs can have different hashes, and environment changes can affect the behavior of identical code. Nor is a runtime UUID the same thing as ML generative abstract typing. The prototype labels it an instance identity and leaves the stronger typing claim open.

Independent publication also needs independent member versions. A family context release can remain stable while an algorithm or constructor gains a new version. The reference registry permits disjoint additions and member-version updates under an immutable family header, while refusing replacement of an existing member/version or distribution/version with different content.

The reverse direction has a current restriction: member wheels embed their family header in provenance, and registry member records also name that family release. Changing a family version or its context therefore requires intentional member releases for members rebuilt under that header, even when their Python code has not changed. A pinned member version cannot be silently republished with the new provenance. Decoupling revisable family context from immutable member records would require a separate metadata model; the reference does not claim that family-context evolution is free of member invalidation.

## Agents as clients of the module language

An agent's practical task is to construct and validate an assembly. Natural language helps formulate the task and retrieve candidate components. Explicit dependency expressions preserve the agent's decision so another process can inspect and replay it.

A minimal interaction is:

```text
discover(problem, candidate budget) → candidate references
inspect(references)                → signatures, context, effects, evidence
plan(requirements, candidates)     → admissible bindings + rejections + gaps
select(plan, explicit choices)     → concrete bindings
lock(bindings)                     → exact internal artifacts
atomic_import(locks, link)         → completed module bundle
evaluate(bundle, workload)         → evidence about that assembly
```

ToolNet explores graph-based tool navigation, while ToolGen represents tool identities within a generative model. These are different approaches to handling large candidate spaces; they do not establish module compatibility. Tool-to-Agent Retrieval studies the value of representing fine-grained tools and their parent agents together. Its 2025 preprint supports testing multilevel discovery rather than assuming a family-level summary is always the best first filter.[^7][^8][^9]

A useful agent interface should therefore expose both family and member retrieval, allow hard constraints before ranking, and return explanations for rejection. “Wrong signature,” “unknown effects,” and “shared type conflict” should be machine-readable results. “No admissible assembly among these retrieved candidates” must remain distinct from “no assembly exists anywhere in the repository.”

The agent can also contribute an existing algorithm by locating its source, choosing a discovery boundary, and supplying a small declaration of context or interface when necessary. Static inventory and build diagnostics then check routine structural facts. It can propose new constructors or adapters when a task requires additional behavior. That is a new build derivation with its own dependencies and evidence, not a hidden runtime patch to a previously published component. An adapter that converts float currency amounts to integer minor units is substantive behavior; its rounding policy belongs in the contract.

No hosted model is necessary to test the mechanical layer. A finite candidate planner and executable examples let us test whether incompatible compositions are rejected and explicit selections replay. A later agent experiment can compare retrieval and synthesis methods while keeping the repository and checker fixed.

## Properties worth establishing

The research should state narrow properties before attempting a broad soundness theorem.

**Closure integrity.** Given an accepted lock, all internal dependency edges lead to artifacts in that lock, and every materialized byte matches its declared artifact. This is conditional on the hash assumption, trusted metadata expectations, and the verifier's implementation. It does not establish publisher authenticity or resolve external Python dependencies.

**Selection preservation.** Replaying the same accepted selection under the same available artifact set returns the same internal graph. Later publication of an alternative implementation cannot silently change the graph. Availability and revocation policy are separate inputs.

**Sharing preservation.** If an accepted binding requires two manifest type references to be equal, their identities are equal in the selected graph and their loaded objects are identical at the checked runtime boundary. The current implementation can test this property; a general language theorem would require a formal loader and module semantics.

**Visibility on failure.** The atomic-import API returns a completed bundle only after its checks and linking finish. An exception returns no partial bundle. This is an API property, not an effect-rollback theorem.

**Constraint monotonicity.** Adding a hard requirement should not admit a binding rejected before. The finite planner can test this directly. Rankings may change within the admissible set without weakening the constraints.

**Relative completeness.** If all combinations of a supplied finite candidate set are explored under decidable predicates, the planner can enumerate all admissible bindings in that set. Search truncation and exploration budgets invalidate a global completeness claim, so the result must report them explicitly.

These are specification targets and tested invariants, not mechanized proofs. A sensible next formal artifact is a small executable semantics and a proof of the finite resolver's soundness relative to declared interfaces. Proving preservation of arbitrary Python program behavior under extraction is a larger and distinct problem.

## Experiments and falsification

The system should be evaluated against simpler alternatives. The key question is whether explicit module families improve agent assembly and maintenance enough to justify their metadata and publication costs.

| Experiment | Baseline | Main measurements |
| --- | --- | --- |
| Authoring scale | Hand-written full inventory, compact discovery defaults, distributed fragments | Declaration bytes, contributor edits, conflict frequency, diagnostic quality |
| Existing-code adaptation | Original package, direct publication, explicit adapter | Source changes, preserved behavior, unsupported constructs, adaptation effort |
| Granularity | Whole package, file-level components, definition cells | Download bytes, artifact count, dependency amplification, incremental rebuild cost |
| Discovery | Flat descriptions, family-first search, joint family/member search | Recall of usable components, context tokens, retrieval latency |
| Composition | Unchecked agent wiring, call-shape checks, type-sharing checks | Valid-plan rate, rejected incompatibilities, downstream task success |
| Enrichment | Handwritten combined service, independent constructors | Behavioral coverage, reuse, order sensitivity, adaptation effort |
| Atomic activation | Sequential imports, prepared bundle, isolated worker | Partial visibility, failure effects, startup cost |
| Evidence | Prose claims, fixtures, independent evaluation records | Incorrect acceptance, abstention, evidence freshness, reproducibility |
| Evolution | Package-wide upgrades, independent member versions | Changed closure size, compatibility failures, migration effort |

Mari provides a substantial existing codebase for migration and granularity experiments. A synthetic megaproject with many independent contributor fragments can test configuration growth, deterministic discovery, conflict detection, and incremental invalidation under controlled edits. The payment family provides a controlled composition experiment with deliberately distinct contracts and shared types. Neither a synthetic build benchmark nor these two families establishes general agent task success or internet-scale repository economics.

Fine-grained publication can fail economically. Each component adds index, licensing, provenance, and dependency overhead. A single edit in a central type library can invalidate many consumers. Search can retrieve plausible but incompatible units. Multiple individually useful enrichments can interact badly. The design must measure these failure modes rather than explain them away as problems a more intelligent agent will solve.

## Reference implementation and remaining work

The reference implements publisher-required TOML declarations, contributor fragments, multiple source roots and module scopes, static discovery, selected build plans, coarse persistent analysis caching, definition compilation, grouped type modules, and independently installable wheels. Its local and authenticated HTTP repositories preserve publisher ownership, reusable interfaces, and exact artifact closures. Runtime signatures, functors, repository resolution, recursive capability synthesis, assembly locks, and wheel-environment locks connect those artifacts to execution. Generated inventories belong to build output. Mari-specific guidance lives in optional migration configuration; the generic compiler also builds unrelated existing Python code and synthetic contribution workloads.

The repository provides authenticated remote publication and public discovery/downloads, with a standard Python Simple index export for local administration. Environment locks retain resolved wheels and a runtime for offline installation and execution in a private environment. It is not a deployed federated service. Distributed snapshots, revocation, evidence attestations, and measured internet-scale operation remain future work; the implemented transport and environment plumbing do not establish the central module-theoretic hypothesis.

The immediate research priorities are the compact build model, contributor composition, explicit adaptation boundaries, and measured behavior preservation for existing Python. Reusable type-family parameters, module expressions stored as repository objects, law-bearing constructors, residual-obligation reporting, and controlled agent assembly evaluations extend that foundation. These should precede claims of a new sound Python type system or demonstrated megascale operation.

## Sources

[^1]: Robert Harper and Benjamin C. Pierce, *Design Considerations for ML-Style Module Systems*, 2005. [Author contents, including signature and module families](https://www.cis.upenn.edu/~bcpierce/attapl/frontmatter.pdf). Full chapter was not inspected here; the companion review separately covers family polymorphism.
[^2]: Scott Kilpatrick, Derek Dreyer, Simon Peyton Jones, and Simon Marlow, *Backpack: Retrofitting Haskell with Interfaces*, POPL 2014. [Author paper](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf).
[^3]: Matthew Flatt and Matthias Felleisen, *Units: Cool Modules for HOT Languages*, PLDI 1998. [Institution-hosted paper](https://people.mpi-sws.org/~dreyer/courses/modules/flatt98.pdf).
[^4]: Anastasiya Kravchuk-Kirilyuk et al., *Persimmon: Nested Family Polymorphism with Extensible Variant Types*, OOPSLA 2024. [Author paper](https://cs.uwaterloo.ca/~yizhou/papers/persimmon-oopsla2024.pdf).
[^5]: Clément Blaudeau, Didier Rémy, and Gabriel Radanne, *Fulfilling OCaml Modules with Transparency*, OOPSLA 2024, and *Avoiding Signature Avoidance in ML Modules with Zippers*, POPL 2025. [Mω paper](https://clement.blaudeau.net/assets/pdf/blaudeau_ocaml_modules.pdf), [ZipML paper](https://clement.blaudeau.net/assets/pdf/zipml.final.pdf).
[^6]: Robert Bruce Findler and Matthias Felleisen, *Contracts for Higher-Order Functions*, ICFP 2002. [Author paper](https://users.cs.northwestern.edu/~robby/pubs/papers/ho-contracts-icfp2002.pdf).
[^7]: Xukun Liu et al., *ToolNet: Connecting Large Language Models with Massive Tools via Tool Graph*, 2024 preprint. [Original paper](https://arxiv.org/abs/2403.00839).
[^8]: Renxi Wang et al., *ToolGen: Unified Tool Retrieval and Calling via Generation*, ICLR 2025; first preprint 2024. [Original paper, revised March 2025](https://arxiv.org/abs/2410.03439).
[^9]: Elias Lumer et al., *Tool-to-Agent Retrieval: Bridging Tools and Agents for Scalable LLM Multi-Agent Systems*, November 2025 preprint. [Original paper](https://arxiv.org/abs/2511.01854).
