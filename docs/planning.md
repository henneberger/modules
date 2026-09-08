# Finite planning of module expressions

`module_families.planning.plan` is the low-level checker for a declared module expression and a finite supplied mapping of candidate aliases to immutable member cards. It performs no imports, downloads, initialization or execution of candidate code. The higher-level `assemblies.resolve_assembly` fetches repository candidates and published interfaces; `synthesis.synthesize` recursively constructs expressions from interface and capability goals. See [goal-directed synthesis](synthesis.md) for that complete workflow.

The expression language has three forms:

```json
{"use": "provider-alias"}
```

```json
{"use": "constructor-alias", "with": {"service": {"use": "provider-alias"}}}
```

```json
{"hole": "chosen-service", "requires": {"id": "example.service", "version": "1"}}
```

An ordinary closed provider has no unfilled requirements. A zero-argument module factory can also be a closed provider when it declares the interface produced by initialization. The planner does not initialize it. A `functor`, a card with nonempty `requires`, or a named module factory must appear with explicit `with` bindings and cannot fill a hole implicitly. Open constructors may be nested explicitly, allowing behavior-enriching compositions with a fixed shape.

Candidate aliases map to built member cards containing `family`, publisher-qualified `id`, `publisher`, `local_id`, `version`, `sha256`, `provides`, and optional `requires`, `effects`, `sharing`, `type_exports`, and behavioral context. Contract references contain exactly `id` and `version`; the checker matches those exactly and does not infer structural subtyping. Repository selectors can separately apply PEP 440 ranges to member releases. A constructor's supplied slot set must exactly match its declared requirements.

```python
from module_families.planning import plan, select

result = plan(expression, candidates, allowed_effects=["local-state"],
              max_solutions=16, max_states=10000)
chosen = select(result)       # succeeds only for one complete admissible solution
chosen = select(result, 1)    # explicit choice among discovered solutions
```

Every occurrence of the same hole name receives the same candidate alias. Reusing a hole with a different required contract is an input error. Different holes vary independently. Domains contain only supplied closed providers matching the exact required reference. Enumeration follows sorted hole names and candidate aliases, so results are reproducible and retrieval scores cannot override hard constraints.

An application's declared effects are unioned with all its children. The `dependency-effects` marker is removed only for an explicitly applied constructor, where child effects substitute for it. On a closed provider the marker remains unresolved. Unknown effects and unresolved dependency effects cannot satisfy any explicit effect policy, even one containing the word `unknown`. With no effect policy they remain visible and create residual obligations. Effect declarations are assertions, not enforced capabilities.

Sharing equations use pairs such as `processor.Money` and `types.Money`. If both children declare the corresponding manifest `type_exports` identities, unequal identities reject the assembly. A missing identity produces a `runtime-sharing` residual. Matching declared strings do not prove Python object identity; runtime checks remain necessary. Behavioral metadata under `behavior`, `assumes`, `guarantees`, `assumptions`, or `obligations` is retained as behavioral residuals. Every selected module also carries a runtime-interface obligation.

The result contains concrete solutions, coherent hole bindings, the resulting provided contract, accumulated effects, selected immutable member references, residual obligations, and machine-readable rejections. Status is `unique`, `ambiguous`, `unsatisfied`, or `incomplete`. An unsatisfied result means no admissible assembly among the supplied candidates under this expression and checker. It is not a statement about the entire repository.

`max_states` bounds the number of complete hole-binding assignments checked. `max_solutions` bounds returned admissible alternatives. If either stops enumeration before all combinations are checked, status is `incomplete` and `complete_for_candidates` is false. A single discovered solution after truncation is never reported as unique. Rejection details are capped at 256 records independently of the search budget, with an omitted-count field. Explicitly selecting an incomplete result chooses a discovered admissible solution while retaining no completeness claim.

Malformed metadata, unknown explicit aliases, malformed expression syntax, and contradictory repeated-hole requirements raise `PlanningError`. Definite compatibility failures produce rejections and an unsatisfied result. Selection without an index is allowed only when exhaustive checking yields exactly one solution; ambiguous or truncated results require an explicit decision.

This checker is an executable finite fragment of the research specification. Recursive constructor synthesis, repository member-version selection, and wheel-environment resolution are implemented in separate layers. Higher-order module unification and behavioral proof checking remain outside the implemented language. The checker records what metadata can decide and what remains to be established.


The command frontend accepts a TOML request; generated machine requests may also use JSON:

```bash
mf plan assembly.toml --out .mf/assembly-plan.json
```

The request contains `expression`, `candidates`, and optional `allowed_effects`, `max_solutions`, and `max_states`. Candidate cards must already identify immutable artifacts; a family source declaration alone is not an artifact selection. The output is JSON and retains the same finite-search and residual-obligation boundaries as the Python API.

For a chosen concrete tree, `module_families.composition.link_expression(expression, exports, candidates, signatures, types=...)` checks and constructs module views from already loaded exports. The runtime-signature mapping is keyed by `(contract_id, version)`. A caller can supply this evaluator to `atomic_import` as its linking callback. It does not import candidate code itself, infer unknown runtime signatures, or cache stateful module instances.

For ordinary repository use, `mf resolve assembly.toml` resolves named selectors in an authored tree, while `mf synthesize goal.toml` searches bounded constructor trees. Both accept `--registry` as a path or HTTP(S) URL and report ambiguity or truncation explicitly. `mf lock-assembly resolution.json --choice 0 --out program.lock.json` commits a selected expression, immutable member locks, exact published interface specifications, effects, and residual obligations. Verification recomputes those checks before instantiation. `mf lock-env program.lock.json --out environment` then resolves prebuilt wheels and records the interpreter fingerprint; `mf sync environment/environment.lock.json --target .mf/venv` installs offline, and `mf exec environment/environment.lock.json --target .mf/venv --export run` executes through that environment. Neither metadata checking nor private-environment execution is an operating-system sandbox.
