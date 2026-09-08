# Executable contribution contracts

Version 0.5.0 connects a missing system requirement to an independently staged candidate, behavioral evaluation, and explicit publication of the tested artifact. It uses the existing module builders and locked Python environments. A contributor can adapt existing code or compose published modules; the acceptance mechanism does not require a new implementation language.

## The work packet

`prepare-contribution` packages an authored TOML contract with its exact published interface. It reads interface metadata only: no provider needs to exist, and no candidate code is imported or fetched. The resulting `task.json` is self-contained and hash-bound. Contributors receive the public boundary and acceptance cases rather than the entire project.

The executable [knowledge contract](../examples/contributions/knowledge.toml) starts with:

```toml
schema_version = 1

[task]
id = "knowledge-batch-retrieval"
summary = "Compose batch retrieval that returns source document identities and text from the supplied corpus"
requires = { id = "knowledge.system", version = "1" }
capabilities = ["batch-retrieval", "diversified-retrieval", "cached-embeddings"]

[policy]
allowed_effects = ["caller-iteration", "memory", "threads"]

[[cases]]
id = "preserve-source-identity"
export = "run"
args = [{"manual:42" = "dogs"}, ["dogs"], 1]
expected = [[{id = "manual:42", text = "dogs", relevance = 1.0}]]
```

The full contract also checks independent queries against one corpus and an empty corpus. These cases are explicit observations about exact inputs. They are not a universal claim about citation fidelity, relevance, caching, or tenant isolation.

A contract must contain at least one uniquely named case. Each case names an interface export and supplies positional JSON-compatible arguments and an exact expected result. Calls must bind to the published call shape. Values must be finite; equality uses canonical JSON, so `1`, `1.0`, and `true` remain distinct. Keyword arguments, tolerances, property generators, hidden evaluations, and external evaluator modules are future extensions. Required keyword-only API parameters currently need an explicit evaluation adapter.

The task requests an exact interface ID/version, capability declarations, and an effect upper bound. Omitting policy permits no declared effects. Capabilities are checked using the existing composition semantics; their presence is not behavioral evidence. Passing output cases does not establish the truth of every capability or effect declaration.

## From synthesis failure to a task draft

```bash
.venv/bin/mf plan-contributions examples/goals/knowledge.toml \
  --registry .mf/repository --out .mf/handoffs
```

The planner retains the original synthesis result and diagnostics. For an observed missing provider with an existing interface declaration, it produces a compact handoff JSON record and an authorable TOML draft. The record includes the interface, originating goal and policy, and a hash binding the draft bytes. The draft has no acceptance cases and is marked `needs-acceptance-cases`.

An integrator must supply meaningful cases before preparing an executable task. The planner does not infer a behavioral specification from a name or capability label, and it does not transfer all parent capabilities onto a missing child.

A failed bounded search is not proof that a provider is missing. The planner checks candidate metadata separately and distinguishes actual absence from malformed candidates, contract conflicts, and search cutoffs. A missing interface needs an interface contract before there can be a provider task. Existing solutions suppress tasks for failed alternative branches. All observations concern the repository as queried; there is no distributed snapshot guarantee.

## Evaluate the exact candidate

A contributor builds and publishes its candidate into a staging repository, resolves its dependencies, locks the complete environment, and synchronizes that environment. Those existing commands are described in the [build guide](build-system.md). Evaluation then uses:

```bash
.venv/bin/mf prepare-contribution examples/contributions/knowledge.toml \
  --registry .mf/repository --out .mf/task
.venv/bin/mf evaluate-contribution .mf/task/task.json \
  .mf/candidate/environment/environment.lock.json \
  --target .mf/candidate/venv --timeout 30 --out .mf/candidate/evidence.json
```

The evaluator verifies the environment and its artifact closure, checks the resulting interface against the sealed task interface, and checks selected capabilities and declared effects. Every case executes in a fresh worker process through the locked interpreter. Mutable module state is not shared across cases; a single exported operation may itself implement a multi-step scenario.

Worker execution has a per-case timeout. A mismatch, execution exception, or timeout records a failed case; any failed case makes the evidence fail. Malformed tasks and incompatible selections fail before evaluation. The CLI writes failed case evidence and returns exit code 2.

The evidence binds:

- The complete task, including interface metadata, cases, and policy.
- The exact selected program lock.
- The environment lock, including all wheel hashes and interpreter constraints.
- Each observed result or failure and the worker timeout.

A fresh process and private dependency environment are not a security sandbox. Candidate Python still has the evaluator user's filesystem and network permissions. The timeout terminates the worker managed by `subprocess.run`; it is not a process-tree, memory, output, or network quota. Use a separately controlled execution service for untrusted contributions.

## Verify and publish

```bash
.venv/bin/mf verify-evidence .mf/task/task.json \
  .mf/candidate/environment/environment.lock.json .mf/candidate/evidence.json
.venv/bin/mf accept-contribution .mf/task/task.json \
  .mf/candidate/environment/environment.lock.json .mf/candidate/evidence.json \
  .mf/candidate/build/index.json --registry .mf/repository \
  --out .mf/candidate/acceptance.json
```

Verification recomputes hashes, rechecks the selection against the task, and checks that every reported case actually matches its expectation. Rehashing a failed report after changing its top-level status does not make it acceptable. Verification does not rerun Python providers.

Acceptance permits publication of exactly the tested root member and its artifact closure. It rejects extra members, changed member metadata, altered artifact records, and unrelated interface declarations. Evidence is contextual: an open constructor has been exercised with the providers in that environment, not every possible future provider. The command returns a receipt connecting publication to the evidence and program.

**These are unsigned local observations.** Hashes bind evidence to artifacts; they do not authenticate who performed the evaluation. Someone who controls the report can fabricate claimed observations. Run evaluation yourself or use an evaluator you trust. Authentication/attestation of evaluation, repository-wide evidence policy, and persistent searchable evidence storage are separate future work. Ordinary `publish` remains available; `accept-contribution` is an explicit evaluator workflow rather than a global admission rule.

## Run the knowledge-base contribution loop

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python examples/contribution_system.py --work-dir .mf/contribution-demo
```

Use an empty directory. Environment locking downloads the existing numerical dependency wheels as needed. To keep resolution offline too, prepare a wheelhouse using the [knowledge example instructions](module-build.md#run-the-real-candidate-system), then pass `--wheelhouse PATH`.

The script:

1. Publishes existing interfaces, Mari MMR, scikit-learn/SQLite/executor adapters, and the open retrieval module.
2. Requests a knowledge system and emits a missing-contribution handoff.
3. Prepares an executable task before any complete system provider exists.
4. Builds two candidates in independent staging repositories using the existing providers.
5. Evaluates a miswired graph: ingestion and retrieval use different SQLite instances. Its API contracts fit, but the behavioral cases fail. Acceptance rejects publication.
6. Evaluates the correctly shared graph. All cases pass, and acceptance publishes that exact candidate.
7. Repeats synthesis against the shared repository, observes the requirement is filled, and replays the accepted program on a new input offline.

Expected summary:

```json
{
  "initial": "needs-contributions",
  "candidates": {"miswired": "failed", "integrator": "passed"},
  "final": "resolved",
  "result": [[{"id": "guide:1", "text": "modules", "relevance": 1.0}]]
}
```

No new retrieval algorithm is written. The candidates are compositions of preserved code and existing adapters. The intentionally incorrect graph omits a sharing constraint; the example demonstrates why behavioral acceptance complements the integration relationships that module authors declare. It does not show the compiler inferring that constraint automatically.

This is a reproducible contribution workflow, not an autonomous agent scheduler or a large-agent benchmark. The next type-system work is specified in the [module calculus design](module-calculus.md): abstract and associated types, sharing, substitution, and identities that survive independent contribution without global name coordination.
