# Goal-directed module synthesis

The public workflow is `goal.toml → synthesize → lock-assembly → lock-env → sync → exec`. An agent supplies an interface, capability labels, and effect policy. The repository supplies published signatures, closed modules, type libraries, and module constructors. The search does not require a handwritten module expression.

In 0.3.0, [compiled open module graphs](module-build.md) are also published constructors. They encapsulate fixed internal wiring and shared node instances; synthesis recursively fills their remaining ports. Graph compilation itself selects providers for an authored topology, rather than inventing arbitrary graphs. Declared semantic index equalities and required identities are checked alongside interface requirements.

In 0.4.0, [checked `.mfl` programs](checked-language.md) supply another kind of published constructor. Their operation bodies have already been checked against typed interfaces before providers are selected. Synthesis fills the module ports and locks the typed interface records. It does not generate new `.mfl` source or verify arbitrary Python provider bodies; those remain an explicit trust boundary.

## Implemented calculus

For a finite repository candidate set, expressions have the form:

```text
p ::= member | constructor(parameter₁ = p₁, …, parameterₙ = pₙ)
```

A published constructor supplies an exact interface and declares named required interfaces. Backward chaining starts at the goal interface, finds candidates supplying it, and recursively discharges their requirements. Closed providers form leaves. Same-interface enrichment is supported: `Retry(client: CLIENT) → CLIENT` can wrap a base client. Constructors may recur across siblings, but the same constructor artifact cannot repeat on one root-to-leaf path. This makes recursion a bounded grammar choice rather than an accidental infinite search.

Each candidate expression is checked for exact interface versions, supplied parameters, declared effect policy, and known type-sharing equations. Capability labels are collected from the selected modules and compared with the goal. These labels are author assertions: collection does not prove that a child capability is exposed usefully by a parent constructor. Application tests and laws remain necessary evidence. An agent should inspect the retained residual obligations before accepting a program.

Search reports `unique`, `ambiguous`, `unsatisfied`, or `incomplete`. Candidate pagination, version constraints, depth, state count, and solution limits are explicit. `complete_for_bounds` concerns only this finite grammar and the repository observations made during that search. It does not establish completeness over arbitrary Python programs or a transactionally frozen global catalog. An explicit `min_artifacts` preference minimizes the number of distinct wheels required by a valid program; it does not measure runtime speed or behavioral quality.

## Interfaces and contributions

Interfaces are separately publishable records containing callable parameter shapes and exported type names. They can be referenced across families. A family groups shared context and conventions; family membership alone is never an interface compatibility check. Each implementation has a publisher-qualified ID and an independently released version.

For example, the iterator family publishes `iterators.chunker@1` and `iterators.unique@1`. Separate more-itertools and Boltons publishers contribute providers. The workflows family publishes constructors requiring those interfaces and supplying `workflows.batch_pipeline@1`. A deduplication goal filters out the plain batching constructor and recursively selects both remaining requirements.

The small workflow functions in `adaptations/workflows` are explicit composition scaffolding. The algorithms are adapted upstream definitions, not newly written substitutes. Their published laws limit the shared example to finite sequences of hashable records and positive integer batch sizes. The original libraries differ on string batching and some invalid inputs; those differences are documented and tested against each source project.

## Program identity and execution

A program lock contains the concrete expression, exact member locks and artifact closures, published interface definitions, effect policy, residual checks, and synthesis goal and bounds. Its hash commits to these values. Lock verification rechecks both repository metadata and the selection obligations, rather than treating a self-consistent JSON hash as evidence of a valid program.

Instantiation verifies and stages the entire selected artifact union before import. Runtime linking checks Python call acceptance and nominal type identity, then creates the module instances and exposes the resulting binding. Factories execute at this stage. Python side effects cannot be rolled back by the module system.

The separate environment lock resolves external wheel dependencies, retains their exact hashes and the execution runtime, and records the Python interpreter fingerprint. Source distribution builds are excluded. Offline synchronization installs this closure into a private environment. Program execution then uses the embedded verified program lock and local wheelhouse without a live repository. Mutable environment files are not an operating-system sandbox.

## Relationship to research

ML's signatures, structures, functors, and sharing equations provide the interface and composition vocabulary. Automatic capability selection is a search layer over that vocabulary, rather than a property obtained merely by adopting ML terminology. The implementation checks a deliberately smaller runtime-oriented language; it does not inherit the soundness proofs of ML, Backpack, or modern family-polymorphism calculi. See the cited [theory review](research-theory.md) and [research proposal](RESEARCH.md).

Still open: richer structural and higher-order signatures, semantic refinements and proof-carrying evidence, parameterized abstract type families, globally consistent repository snapshots, stronger search optimization, and empirical measurement of agent-generated adaptations. These research questions are separate from the implemented build, publish, synthesize, lock, and execution path.

## Missing requirements as contribution work

`mf plan-contributions GOAL --registry REPOSITORY --out DIRECTORY` retains synthesis diagnostics and prepares authorable handoffs for observed missing providers with published interfaces. It does not turn search cutoffs into absence claims or invent behavioral acceptance cases. See the [executable contribution workflow](contributions.md).


## Incremental publication and bounded discovery

Synthesis compares the repository publication revision before and after discovery.
If publication changes during the search, `status` becomes `incomplete`,
`complete_for_bounds` is false, and `truncation` includes `repository_changed`.
The result is not automatically lockable. The contribution planner does not
interpret this changing view as proof that a provider is absent. Local, HTTP,
federated, and staged candidate views all participate in this check.

This guards against skipped or duplicated offset pages without retaining a
historical catalog snapshot. It does not guarantee progress during continuous
publication; callers can retry, and a retained-snapshot protocol remains future
work for sustained write-heavy deployments.

An exact `goal.root` is read by immutable identity instead of enumerating unrelated
providers. The root must still satisfy the declared interface and hash. Its open
dependencies use ordinary bounded synthesis, including type, effect, and evidence
checks. This lets the evaluator test one newly submitted candidate even when the
family contains more providers than the discovery budget.

[Candidate paging measurements](candidate-paging.md) verify that a one-card page
from a million-row local catalog decodes one card. Broad version predicates and
deep offsets can still scan scalar index entries; bounded memory is not a promise
of constant query time for every predicate.
