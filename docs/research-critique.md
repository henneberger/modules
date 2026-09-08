# Critical review and falsifiable research questions

Research date: 2026-09-08. This is an independent review of the proposed module-family repository as a research system. The conclusions below are design arguments and experiment proposals; they are not measurements of the current implementation or proofs about arbitrary Python programs.

## The strongest contribution to investigate

The interesting hypothesis is that a repository can expose **problem context, compatibility evidence, and compositional requirements at a smaller granularity than conventional packages**, enabling an agent to acquire an appropriate implementation and produce a verifiable linking plan. A large collection of tiny wheels is a useful experimental substrate. Its count, by itself, would not establish the hypothesis.

The evaluation should ask whether the additional information improves successful task completion or lowers total engineering cost relative to established package and tool workflows. It should not assume that an agent's general capability implies reliable selection, that more metadata necessarily helps, or that smaller artifacts are always cheaper.

## Separate retrieval, selection, linking, and task success

ToolRet, published at ACL 2025, studies tool retrieval with 7.6k tasks and 43k tools. The authors report that models performing well on general information-retrieval benchmarks can perform poorly on tool retrieval, and that retrieval errors degrade downstream tool-use success. The relevance to this proposal is methodological: finding a plausible description is a distinct failure boundary from invoking the right implementation. The paper does not evaluate this Python module system. [Shi et al., ToolRet](https://arxiv.org/abs/2503.01763)

Measure at least five stages separately:

1. Does the retrieved set contain a valid implementation?
2. Does selection choose one satisfying all declared hard constraints?
3. Does the proposed binding graph pass mechanical checks?
4. Can the selected closure actually install, load, and execute in the target environment?
5. Does execution satisfy an independently evaluated task objective?

The product of these stages can be much lower than any one score. Include tasks with no suitable implementation and measure justified abstention. Invalid plans, unsupported environment claims, and selecting an incompatible provider should remain visible even if a forgiving task harness can recover afterward. Function calling evaluations such as BFCL illustrate the need to test multiple stages and interaction conditions; their scores should not be used as a proxy for repository selection quality. [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html)

## Family context must help without hiding members

Hierarchical discovery can reduce context cost, but a family summary can also become a recall bottleneck. A broad "knowledge systems" family may contain a useful statistical estimator that its summary does not mention. Conversely, copying the same broad family text onto every member can swamp distinctions between implementations.

The 2025 *Tool-to-Agent Retrieval* preprint explicitly represents tools and parent agents together and reports better retrieval than coarse agent-level descriptions on its benchmark. This is relevant evidence for testing fine-grained and family-level representations together, not proof that one indexing strategy generalizes to local algorithm discovery. [Lumer et al.](https://arxiv.org/abs/2511.01854)

Compare flat member search, family-first search, and combined retrieval. Track family-routing recall separately. Evaluate text-budget allocation between shared context and member-specific details. Allow a task to discover a member through more than one family or problem relation; a single fixed taxonomy is a hypothesis, not a necessary semantic rule.

## Constraint solving is harder than closure traversal

For a fixed acyclic artifact graph with exact dependency choices, computing the reachable closure is straightforward. Selecting versions and interchangeable providers under conflicts is a different problem. EDOS/Mancoosi models alternatives and conflicts explicitly, including expansion of version constraints and virtual package providers. That work is a better precedent for a general repository resolver than unconstrained greedy ranking. [Treinen and Zacchiroli, EDOS to Mancoosi](https://www.mancoosi.org/papers/debconf8.pdf)

A formal account should state:

- the finite repository snapshot against which decisions are made;
- which candidate domains are complete and which have been truncated by retrieval;
- exact contract/version compatibility rules;
- shared-type and provider-identity constraints;
- installation conflicts and external environment assumptions;
- the objective used after feasibility, such as cost or measured task quality;
- whether the solver is complete, approximate, or bounded by search limits.

A bounded agent planner can be useful without being complete. When its candidate search is truncated, "no plan found" must not be silently upgraded to "no valid implementation exists." An exact module lock proves replay relative to chosen repository records; it does not certify that the chosen plan is optimal or globally satisfiable with unresolved Python dependencies.

### Closed providers and module constructors have different types

A component that already provides interface `Σ` is different from a constructor whose result will provide `Σ` once requirements `R` are filled. A useful notation is `Provider(Σ)` versus `Constructor(R → Σ)`. A retry constructor cannot fill its own underlying-processor slot merely because its advertised result signature is a charge service. Metadata must preserve `contract_target`, required slots, and residual obligations through retrieval and planning. A finite candidate planner that does not recursively instantiate constructors should report them as open expressions, not usable providers.

The module-expression grammar also needs variables as expressions, so a functor body can refer to its parameters and a `let` body to the introduced module. A grammar, typing judgments, operational semantics, and preservation theorem are different artifacts. The initial design can provide the first two as research targets without implying that a formal semantics or proof already exists.

Effect descriptions need a similar distinction. Importing or applying a factory can have initialization effects, while the returned operations have latent effects only when called. Wrappers can propagate dependency effects or add their own. A single flat `effects` list is a practical conservative filter, but it does not yet describe that higher-order composition algebra. An eventual account should state separate initialization and operation effects and propagate unknowns explicitly.

## Contract authority and conformance are separate

Two providers can assert the same contract identifier while disagreeing about behavior. Two distinct authorities can also publish identical-looking identifiers. A public repository therefore needs a model for who defines a contract, who may assert conformance, which evidence supports that claim, and whose attestations a client accepts.

For payments, identical Python shapes can conceal different currency units, rounding, retry behavior, idempotency scope, or credential domains. Runtime signature checks can reject an incorrect call shape. Nominal identities and explicit sharing checks can reject a known token-domain mismatch. Neither mechanism proves that all implementations satisfy the same behavioral laws. The formal core should keep conformance evidence and behavioral assumptions explicit.

SLSA artifact verification similarly depends on expected identities and parameters, not merely the existence of a signature. This supports distinguishing artifact identity, publishing authority, contract authority, and conformance evidence in the repository model. [SLSA v1.2 verification](https://slsa.dev/spec/v1.2/verifying-artifacts)

The current local repository assumes trusted control of its SQLite records and publication directory. It checks bytes and declared identities; it does not authenticate a global namespace or verify semantic conformance. Its tests should be described within that assumption.

## Atomic dependency bundles require a precise observable boundary

"Import this dependency bundle atomically" has at least three possible meanings:

1. **Coherent selection:** every requirement resolves against one repository snapshot into a mutually compatible graph, including type-sharing constraints.
2. **Atomic binding visibility:** the application receives the new collection of bindings only after every required implementation has loaded and passed linking checks.
3. **Transactional execution:** failed loading reverses every effect of Python module initialization and factory execution.

The first two are appropriate goals for a reference implementation. The third is generally unavailable for arbitrary in-process Python. Python imports execute module code and populate a shared module cache; failures do not remove every module imported as a side effect. Deleting selected cache entries would not undo network requests, writes, registrations, mutated objects, or references retained elsewhere. [Python import system](https://docs.python.org/3/reference/import.html)

An honest two-phase design prepares and verifies a complete immutable plan, loads into a private binding map, checks required exports and sharing identities, and then returns or installs the completed map as one application-visible bundle. Failure publishes no partial bundle. It may still leave import or factory effects. Stronger containment requires a separately controlled process/runtime and explicit communication of the resulting services or values; it does not follow from a Python context manager.

Reusable component-type libraries make the coherence obligation more significant. If an index consumes the same `Document` type that a parser produces, both bindings must refer to the agreed nominal type origin, not merely equivalent field shapes. Alternative dependency choices must propagate equalities across the entire graph. A locally attractive provider may make a distant sharing constraint unsatisfiable.

Module-producing modules add another distinction: a factory's published artifact identifies its code, a closed application identifies its chosen argument graph and configuration, and a runtime instance may introduce fresh state or abstract identity. Caching the closed application does not authorize sharing credential-bearing objects or fresh abstract types. A formal account should state which factories preserve result identities for equal inputs and which intentionally allocate new identities. Calling these "applicative" or "generative" is justified only if their observable identity rules have actually been specified.

An import-bundle digest that commits only to selected locks identifies an artifact environment. It does not identify an arbitrary Python linking callback or the callback's configuration. Likewise, a runtime composition hash over caller-chosen factory names and module labels is a logical binding label unless those names are themselves bound to verified implementation identities. Reproducible assembly identity needs an explicit constructor expression, artifact references, configuration, and rules for generative instances.

## Fine-grained distribution has costs as well as benefits

For a selected closure, let `B` be transferred implementation bytes, `M` transferred metadata bytes, `n` artifact count, `h` average request/installation overhead, and `T` selection/verification time. A useful first cost model is:

`total acquisition cost ≈ transfer(B + M) + n × h + T`.

This is an analysis model, not a measured performance formula. Splitting may reduce `B` while increasing `M`, `n`, and `T`. Content-addressed shared cells reduce duplication only when their identity policy permits reuse and consumers share caches. Tiny functions whose closures contain large numerical libraries may produce almost no environment-level savings. A common support-cell update can invalidate many downstream identities and releases.

Measure multiple partitions of the same source: original package, module-level packages, definition-level packages, and definition facades with shared cells. Compare cold and warm caches, local and simulated remote latency, total environment size including third-party dependencies, and republishing after localized edits. Report catalog overhead, verification work, license duplication, and update fan-out along with source-byte savings.

## Identity reuse can change runtime meaning

Unison's hash model separates names from definition identity. In Python, code identity, module-object identity, instance identity, and credential authority must still be separated. Deduplicating byte-identical stateful modules can cause globals, caches, registries, or random generators to become shared. Deliberately separating a shared class can break identity checks, pickling, or plugin registrations. [Unison identity model](https://www.unison-lang.org/docs/the-big-idea/)

A compiler correctness claim needs an explicit supported language subset and an observational model: which behaviors are preserved, which reflection is excluded, and how module initialization is transformed. Differential algorithm outputs are valuable evidence but do not cover initialization order, monkeypatching, dynamic imports, serializers, or concurrency. The experimental suite should include representative edge cases and report rejected constructs, rather than treating source conversion coverage as semantic proof.

## Evidence-aware selection needs evidence governance

An implementation author's benchmark is useful context but may not compare candidates fairly. Record task distribution, input size, hardware, dependency versions, random seeds, metric definitions, evaluator identity, and the artifacts evaluated. Distinguish declared complexity from measured latency and from modeled cost. New observations should attach to unchanged artifact identities, with time and provenance, so the repository need not reissue code merely to update an assessment.

Prefer held-out tasks with independently written acceptance criteria. A benchmark generated from the same member descriptions used by retrieval risks rewarding paraphrase matching. Keep unknown effects and undocumented limitations as unknowns; substituting optimistic defaults would make metadata completeness appear to be semantic safety. MCP's explicit treatment of tool annotations as untrusted is an appropriate baseline for publisher-provided effect claims. [MCP tool annotations](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

## Proposed experiment matrix

| Research question | Controlled comparison | Primary outcome | Failure that would weaken the proposal |
| --- | --- | --- | --- |
| Does family context help selection? | Same tasks and models; names/docstrings versus member cards versus shared family context | Task success, recall, input tokens | More context costs more but does not improve held-out outcomes |
| Do explicit contracts help composition? | Same candidates; unchecked calls versus structural checks versus nominal sharing checks | Invalid bindings prevented before execution | Correct providers rejected frequently or semantic errors remain invisible |
| Does finer publication reduce acquisition cost? | Same implementation at package/module/definition partitions | Full acquisition latency and environment bytes | Metadata and dependency overhead dominate useful savings |
| Can agents use evidence rather than descriptions alone? | Description-only versus provenance-linked evaluation records | Choice quality across shifted workloads | Rankings follow marketing text or stale benchmark winners |
| Does the system generalize beyond Mari? | Numerical algorithms, parsing, local state, and simulated payment providers | Coverage and task success per domain | Metadata/compiler assumptions specialize to one repository |
| Does bounded discovery degrade gracefully? | Increasing distractors, missing providers, and adversarially similar names | Abstention, recall, plan validity, latency | More candidates produce confident invalid selections |

These experiments should precede claims about a globally deployed repository. The local registry, deterministic artifacts, explicit planner, and candidate project make the experiments repeatable. They establish a research testbed whose value can be measured, while a future transport and trust layer remains a separate engineering and research effort.

## References

All web sources were checked on 2026-09-08. Publication dates are distinguished from access dates; project specifications are maintained documents rather than experimental results.

1. Zhengliang Shi, Yuhan Wang, Lingyong Yan, Pengjie Ren, Shuaiqiang Wang, Dawei Yin, and Zhaochun Ren. *Retrieval Models Aren't Tool-Savvy: Benchmarking Tool Retrieval for Large Language Models*. ACL 2025; arXiv:2503.01763, revised May 26, 2025. [Paper](https://arxiv.org/abs/2503.01763).
2. Elias Lumer, Faheem Nizar, Anmol Gulati, Pradeep Honaganahalli Basavaraju, and Vamse Kumar Subbiah. *Tool-to-Agent Retrieval: Bridging Tools and Agents for Scalable LLM Multi-Agent Systems*. arXiv:2511.01854, revised November 4, 2025. Preprint; no peer-review claim is made here. [Paper](https://arxiv.org/abs/2511.01854).
3. Ralf Treinen and Stefano Zacchiroli. *Solving Package Dependencies: from EDOS to Mancoosi*. DebConf8 presentation, August 10, 2008. [Authors' slides](https://www.mancoosi.org/papers/debconf8.pdf).
4. Berkeley Gorilla project. *Berkeley Function Calling Leaderboard V4*. Maintained benchmark site. [Project](https://gorilla.cs.berkeley.edu/leaderboard.html).
5. SLSA project. *Build: Verifying Artifacts*, specification v1.2. [Specification](https://slsa.dev/spec/v1.2/verifying-artifacts).
6. Python Software Foundation. *The Import System*, Python 3 language reference. [Documentation](https://docs.python.org/3/reference/import.html).
7. Unison project. *The Big Idea*. Maintained language documentation. [Documentation](https://www.unison-lang.org/docs/the-big-idea/).
8. Model Context Protocol project. *Tools*, protocol revision 2025-11-25. [Specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
