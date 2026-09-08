# A module calculus for independent contributions

This is the design for the next type-system increment, not a description of syntax already accepted by the compiler. The executable baseline is [the checked language](checked-language.md) and [open TOML module graphs](module-build.md). The contribution workflow can distribute and evaluate work against those existing contracts while the richer calculus is implemented.

The objective is to check a contribution using only the assumptions it needs, then preserve that check when another agent supplies those assumptions. A retrieval developer should not need to inspect every encoder, index, document store, and citation renderer that might eventually be selected.

Separate checking above an existing implementation language follows the architectural precedent of Backpack. Its paper defines a module language, elaboration, and soundness result; this project does not inherit that result merely by adopting a similar architecture. [Backpack, POPL 2014](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf).

## What is implemented, and what changes

| Concern | Executable 0.4 foundation | Proposed increment |
| --- | --- | --- |
| Value identity | Explicit nominal strings in `typing.types` | Scoped abstract types and associated projections |
| Module relationships | Python type sharing and opaque semantic-index equalities | One typed equality language with substitution through signatures |
| Constructors | Published constructors with explicit required ports | Constructors whose result types depend on argument types |
| Composition | Acyclic graphs, partial linking, explicit export projection | The same graph discipline with abstract-type elaboration |
| Resource usage | Shared, affine, linear; synchronous call borrows | Preserve those rules after type substitution |
| Implementation confidence | Checked orchestration plus trusted Python | Independently recorded conformance evidence; still a trusted boundary |
| Initialization | Fixed topological Python wiring | Keep this discipline; recursive initialization is a separate problem |

Semantic indices already reject a declared mismatch between an encoder's `Space` and an index's `Space`. They are strings, however: the checked value language cannot yet express `Vector[Embedding.Space]`. Exported Python classes are another distinct mechanism. The next checker must connect value types to module relationships instead of treating these as parallel metadata systems.

## A knowledge-base example

The following is proposed notation, not executable `.mfl`:

```text
signature EMBEDDING {
  type Space : identity
  embed : Text -> Vector[Space] ! {model.read}
}

signature DOCUMENTS {
  type DocumentId : value(shared)
  load : DocumentId -> Document ! {storage.read}
}

signature INDEX {
  type Space : identity
  type DocumentId : value(shared)
  search : Vector[Space] -> List[DocumentId] ! {storage.read}
}

signature CITATIONS {
  type DocumentId : value(shared)
  render : DocumentId -> Citation[DocumentId] ! {storage.read}
}

constructor Retrieval(
  E : EMBEDDING,
  D : DOCUMENTS,
  I : INDEX where Space = E.Space, DocumentId = D.DocumentId,
  C : CITATIONS where DocumentId = D.DocumentId
) -> {
  type DocumentId = D.DocumentId
  answer : Text -> List[Citation[DocumentId]]
    ! {model.read, storage.read}
}
```

The implementation can embed a query, search the index, load the referenced documents, and render citations. The checker rejects an encoder/index space mismatch even if both use arrays of 768 floats. It rejects citations from a different document-ID domain even if both IDs are Python strings. Neither error is visible in the call signature `search(list[float]) -> list[str]`.

The equations do not establish retrieval quality, citation faithfulness, or access-control correctness. A malicious provider can label one encoder as another. Those are implementation obligations and require evidence. Types establish which declared domains may be composed; evaluations establish facts about particular implementations and workloads.

The signatures also expose what a contributor does not need to know. An index implementation needs the identity of its vector space and document domain; it need not know the concrete document-store class or the rest of the answering pipeline. An agent can implement a caching wrapper with `type Space = base.Space` and preserve compatibility without coordinating a new global type name.

## Core terms and environments

Start with a first-order, acyclic language. Defer higher-kinded types, recursive signatures, arbitrary type-level computation, and implicit coercions.

```text
κ ::= identity | value(u)                    kinds
u ::= shared | affine | linear              value usage
τ ::= α | n | p.t | K[τ₁, …, τₙ]             type terms
S ::= { type t : κ; op : (mode τ)* -> τ* ! ε; equations }
F ::= (X₁:S₁, …, Xₙ:Sₙ) -> S                constructor signatures
m ::= p | F(m₁, …, mₙ) | new F(m₁, …, mₙ)   module expressions
```

Here `α` is a scoped abstract binder, `n` a rigid nominal identity, `p.t` a module-associated type, and `K` a registered type constructor with a fixed arity and kind signature. `Vector` has kind `identity -> value(shared)` in this example; this is a contract promise about how checked code may use its values, not a declaration that a mutable Python array is immutable.

`Σ` contains signatures, constructor declarations, type-constructor kinds, and rigid identities. `Γ` maps module paths to signatures and identities. `E` contains well-kinded sharing equations. The existing value checker retains its separate shared-variable context and owned-variable context.

An abstract declaration introduces a fresh, rigid symbol while checking a generic body. It is universally unknown to that body: the implementation may use its declared operations but cannot decide that `E.Space` equals some preferred encoder's identity. During constructor application, those bound projections are replaced by the supplied module's witnesses.

An opaque exported type packages a witness behind a module path. Consumers may name `M.t` and use exported operations without learning its Python representation. A manifest alias, such as `type Space = base.Space`, preserves equality and does not introduce a fresh witness. Abstractness and generation are distinct: an opaque export can have a stable applicative identity.

## Equality, substitution, and checking

Use alpha-renamed binders and capture-avoiding substitution. A substitution `θ` replaces formal module paths and their associated projections with actual paths or canonical type terms. It applies to operation parameters, results, result aliases, nested constructor requirements, and every sharing equation. It does not change ownership modes or silently erase effects.

Equality is the least congruence generated by explicit aliases and sharing constraints. There is no equality from matching array dimensions, identical Python representation, equal capability labels, or successful tests.

Inference-rule sketches:

```text
(Projection)   Γ(p) contains type t : κ
               ---------------------------
               Σ; Γ ⊢ p.t : κ

(Congruence)   E ⊢ τ₁ ≡ σ₁ ... E ⊢ τₙ ≡ σₙ
               --------------------------------
               E ⊢ K[τ₁,...,τₙ] ≡ K[σ₁,...,σₙ]

(Operation)    required parameters/results equal actual ones under E
               ownership modes equal; actual effects ⊆ required effects
               --------------------------------------------------------
               actual operation satisfies required operation

(Application)  Γ ⊢ F : (X₁:S₁,...,Xₙ:Sₙ) -> S
               θ = [X₁ ↦ M₁,...,Xₙ ↦ Mₙ]
               Γ; E ⊢ Mᵢ satisfies θ(Sᵢ), for every i
               E entails all substituted constructor equations
               ----------------------------------------------------
               Γ; E ⊢ F(M₁,...,Mₙ) : θ(S)
```

Initially require invariant parameter and result types. Rich function subtyping is not necessary to make separate contribution useful and would interact with usage and effects. A provider may have private extra exports, but a sealed module view exposes exactly the required public signature.

Constraint solving must distinguish flexible holes introduced by an unfilled port from rigid identities exported by selected modules. Solving `?Space = encoder.Space` is allowed. Equating two distinct rigid encoder identities is an error. Alpha-renaming two copies of a constructor must not merge their unrelated holes accidentally.

For first-order terms, implement kind checking and unification with an occurs check, rigid-identity conflict detection, and a recorded reason for each equality. A failed link should show both declaration paths and the equation that connected them. An open composition may export unresolved constraints; a closed composition must discharge them. Retain the complete normalized signature and equations in its build artifact so separate checking survives publication.

## Applicative identity and generativity

Default to applicative module type identities for reproducible builds. The proposed canonical identity includes the constructor's immutable artifact/member identity, normalized semantic configuration, identities of its module arguments, and the exported type label. Reapplying the same constructor to the same arguments yields the same associated type identities. Equal type identities do not imply the same runtime instance.

Configuration affecting a representation or domain must participate in identity. Changing encoder weights, tokenization, normalization, or distance convention cannot reuse a vector-space identity merely because the Python entry point and dimensions are unchanged. The compiler can require declared configuration to be included; it cannot discover hidden model configuration inside arbitrary Python.

Generative `new` introduces fresh type witnesses for a distinct instantiation. This is useful for an isolated tenant document domain or a fresh resource protocol. A build-scoped generation binder must be explicit in the normalized graph and lock, rather than depending on nondeterministic compiler state. Two `new` nodes have distinct binders. Replaying one locked graph reproduces its build-time type identities; it does not prove that runtime resources from two executions are interchangeable.

Fresh types per runtime invocation would require existential result packaging and runtime witnesses that preserve freshness. Defer that feature rather than presenting a build hash as runtime generativity. Existing owned handles enforce consumption, not fresh associated types per factory invocation.

Instance sharing remains its own relation. Two clients wired to one store node receive one store instance; two applications may share an abstract document-ID type while using separate connections. Conflating those facts could turn a correct type link into an incorrect transaction protocol.

## Checked bodies and the Python boundary

After module substitution, feed normalized value types into the existing `.mfl` usage checker. A value of `Vector[E.Space]` can flow to `I.search` only when `E.Space = I.Space`. A linear `Session[D.DocumentId]` must still be consumed on every normal return path; changing its type argument must not create a new owner or allow a duplicate use.

Shared and linear programming can coexist without imposing linear discipline on every ordinary value; Linear Haskell is a useful primary reference for that design space. This project uses a smaller restricted composition language and does not implement that paper's polymorphic core calculus. [Linear Haskell, POPL 2018](https://arxiv.org/abs/1710.09756).

Generated Python can erase phantom type arguments where no dynamic witness is needed, while retaining normalized descriptors at trusted adapter boundaries and in build records. Opaque values still need either trusted implementations or suitable runtime wrappers. No inference rule above proves that a Python function honors a phantom vector-space label, releases resources on exceptions, reports its effects honestly, or terminates.

The intended theorem to investigate is conditional: a well-typed open composition remains well-typed after substituting implementations satisfying its assumptions. A subsequent elaboration theorem would relate checked source to generated guarded Python under an explicit adapter model. These are research and implementation obligations, not established results in this repository.

## Contribution contracts and evidence

A contribution packet should contain the local required/result signatures, abstract binders, residual sharing equations, permitted effects, acceptance obligations, and references to immutable fixtures. It must preserve binder scope when copied to another agent. Sending only flattened operation names would lose the very assumptions that make independent development possible.

Keep two different judgments:

```text
Σ; Γ; E ⊢ module : signature             compatibility under assumptions
evidence supports obligation for artifact and evaluation inputs
```

An evidence record should bind the exact implementation artifact, contract, evaluator, fixtures, result, and relevant execution configuration. Passing a citation-preservation fixture is evidence for that fixture, not a proof for all documents. Failing it need not change the module's type: the candidate may be type-correct and behaviorally unacceptable. Conversely, strong retrieval scores cannot waive a type mismatch.

An integrator can require both judgments before selecting a contribution. Evaluation policies may differ between projects using the same family. A repository can accept independently published candidates without declaring every candidate suitable for every consumer. A contributor's claim and an evaluator's observation must remain distinguishable, and content hashes alone do not authenticate an evaluator.

The executable contribution workflow should first bind and run these obligations using current interfaces. Add abstract binders to that packet format only when the checker can validate them. Do not introduce unvalidated future syntax into ordinary published contracts.

## Why this can reduce coordination

The unit of coordination becomes a contract and its residual obligations. Agents can work on encoder adapters, citation renderers, indexes, and cache constructors independently. A parent composition aggregates their checked interfaces rather than importing all of their source into every worker's context.

This creates an opportunity for bounded context per contribution and reusable checked subcompositions. It is not a guarantee that total search or coordination is bounded. Widely connected type equalities, ambiguous providers, contract changes, and expensive evaluations can still dominate. Search should propagate type conflicts before fetching code, memoize normalized subgoals, and expose unsatisfied requirements as new work packets.

Million-agent claims require measurement: context size per work packet, integration success without human edits, cache reuse, conflict locality, candidate-search growth, and evaluation cost. A type system removes certain invalid combinations from consideration; it does not make all possible combinations cheap to explore.

## Implementation milestones and acceptance cases

1. **Canonical type IR.** Add kinds, scoped binders, projections, and constructor applications to a versioned schema. Reject unknown constructors, wrong arity, ill-kinded applications, cyclic aliases, and capture during normalization. Equivalent alpha-renamings must serialize to equivalent canonical forms.
2. **Separate signature checking.** Type-check a retrieval constructor with no encoder/index/store implementations published. Reject a body that assumes its abstract space equals a particular encoder identity. The report must record all assumptions and remain deterministic without importing Python.
3. **Substitution through graph linking.** Link an encoder, index, store, and citations under the example equations. Reject equal-dimensional distinct spaces and distinct string-backed document domains. Preserve open equations through partial publication and nested constructor application.
4. **Identity semantics.** Repeated applicative applications with identical inputs share type identities; different semantic configurations do not. Two explicit generative binders differ. Reusing a node preserves instance sharing, while duplicate applicative nodes must not imply one runtime instance.
5. **Value integration.** Reject feeding `Vector[Other.Space]` into the selected index. Reject copying or dropping a linear parameterized owner exactly as for current nominal owners. Verify that type normalization cannot weaken modes or increase allowed effects.
6. **Contribution evidence.** Accept a locally valid candidate, run its pinned obligations, and select it only under the integrator's evidence policy. Changing the artifact or fixture must invalidate reuse of old evidence. A passing test must never merge two rigid types.
7. **Preservation and scale experiments.** Property-test normalization/substitution and nested-link equivalence, develop the conditional preservation argument, and benchmark independent contributions to a real knowledge base. Publish measured limits alongside the result.

The first useful delivery is milestones 1–3 with executable negative cases, alongside an independent contribution/evaluation loop using existing contracts. Generativity, sophisticated subtyping, recursive modules, and stronger proofs should follow explicit use cases rather than becoming prerequisites for that delivery.
