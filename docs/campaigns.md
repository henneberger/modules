# Contribution campaigns

A campaign connects explicit work contracts to contribution workers, trusted
evaluation, and final program synthesis. It is the executable loop between
“this project needs these implementations” and “these accepted implementations
compose into this locked program.”

For a knowledge-base project, an integrator might request a citation-preserving
PDF reader, a vector index, and a retrieval constructor. The retrieval task can
wait for the reader and index to pass their own acceptance contracts. Its driver
then receives a small task instead of the source and conversational history of
every contributor. A final retrieval evaluation still needs to check the whole
composition: individual reader and index evaluations do not establish retrieval
quality or correct citations in combination.

This implementation runs bounded **sequential** driver invocations and evaluator
passes against a durable queue. It can use a local SQLite queue or an authenticated
HTTP queue shared with external workers. It is not a distributed process scheduler,
and it does not demonstrate millions of simultaneously running agents.

## Author the work, dependencies, and limits

```toml
schema_version = 1

[campaign]
id = "knowledge-base"
goal = "goals/knowledge.toml"
driver = "drivers/contributor.toml"

[limits]
rounds = 20
workers_per_round = 4
evaluations_per_round = 4
driver_timeout = 300
evaluation_timeout = 60
lease_seconds = 60

[[tasks]]
id = "reader"
manifest = "contracts/reader.toml"
max_attempts = 3

[[tasks]]
id = "index"
manifest = "contracts/index.toml"
max_attempts = 3

[[tasks]]
id = "retrieval"
manifest = "contracts/retrieval.toml"
prerequisites = ["reader", "index"]
max_attempts = 3
priority = 10
```

Each task manifest is a normal [executable contribution contract](contributions.md):
its interface requirement, capability requirements, effect policy, and explicitly
authored acceptance cases. Interfaces must already be published in the accepted
repository. Campaigns do not infer useful tests from API signatures or convert a
capability description into proof of behavior.

Tasks may appear in any order; the coordinator checks their prerequisite graph
before enqueueing. Unknown references and cycles are rejected. A prerequisite
unblocks its dependents only when the evaluator has accepted its contribution.
The campaign supports up to 1,000 explicit tasks; this authoring limit is separate
from the queue's measured storage capacity.

## Configure the agent driver

The operator supplies a TOML argument vector:

```toml
[driver]
command = [
  "python3", "{campaign}/drivers/contribute.py",
  "--task", "{task}",
  "--feedback", "{feedback}",
  "--context", "{context}",
  "--proposal", "{proposal}",
  "--workspace", "{workspace}",
]
```

`{campaign}` is the campaign manifest's directory. The remaining placeholders
refer to a private work directory for this attempt. `task.json` is the immutable
prepared contract. `feedback.json` supplies the attempt number and the previous
rejection or worker failure message, including bounded observed case results.
`previous_submission` preserves the immediately preceding submitted candidate
identity (or worker error), so a repair agent can distinguish rejected versions.
The task hash does not change on a retry.
The task payload never chooses the executable or command arguments.

Driver processes receive a small environment allowlist for operating-system
basics and operator model authentication (`CODEX_HOME` and `OPENAI_API_KEY`). They
do not inherit arbitrary evaluator secrets or repository/queue tokens. The CLI
also removes explicitly configured secret/token variable names, including names
that would otherwise be on that allowlist. The Python API accepts an explicit
`driver_env` mapping when an operator needs a different environment. This controls
environment inheritance; the host filesystem still requires separate isolation
when executing untrusted code.

`context.json` gives the driver bounded metadata from the accepted repository:
matching providers, ranked summary/capability search results, a small inventory
sample, and the selected candidates' required interface declarations. Its default
limits are 64 candidates, 32 interfaces, four dependency levels, and 1 MB. It
contains fixed member identities and contracts for composing a graph or checked
program; it does not fetch source or import implementations. Truncation diagnostics
make clear that this context is incomplete discovery, not proof that another
provider does not exist. The driver can propose composition instead of rewriting
an available implementation.

The driver writes ordinary Python and a family, graph, or checked-program
manifest, then writes a proposal such as:

```json
{"kind": "graph", "manifest": "retrieval.toml"}
```

The manifest must reside inside the attempt workspace. The worker builds it and
publishes its candidate artifact into **staging**. A revised implementation needs
a new immutable member version; an agent cannot overwrite the previous attempt's
published identity. The bundled Codex driver uses a task-specific member ID and an attempt-specific
member version, preserving the family header. Custom drivers must also publish
repairs under fresh member versions. The campaign regression test rejects an
incorrect increment implementation, repairs it from actual evaluator feedback,
retains both staged versions, admits only the passing version, and locks its
composition with the independently contributed dependency.

Drivers and candidate Python are executable code. Run untrusted implementations
inside an operator-provided sandbox or isolated worker host. A virtual environment
isolates dependency installations; it does not restrict filesystem or network
access.

## Run and resume

Configure an evaluator signing key through the environment. Ed25519 private keys
use `ed25519:<private-key-hex>`; public verification keys can be distributed without
sharing signing authority. The existing shared-secret HMAC mode remains available
for a single explicitly trusted evaluator domain.

```sh
.venv/bin/mf campaign campaign.toml \
  --queue .mf/knowledge-campaign/queue.sqlite \
  --registry .mf/accepted \
  --staging .mf/candidates \
  --work-dir .mf/knowledge-campaign/work \
  --evidence-store .mf/knowledge-campaign/evidence.sqlite \
  --evaluator knowledge-evaluator \
  --key-env MF_EVALUATOR_SECRET \
  --find-links .mf/upstream-wheels \
  --no-index
```

Use a dedicated campaign queue. The coordinator refuses a queue containing
unrelated task IDs because the queue's general worker claim operation is not
project-filtered. Campaign IDs namespace its queued task identities. For HTTP,
pass the service URL to `--queue` and provide its administrator token through
`MF_QUEUE_TOKEN`; the coordinator needs enqueue, inspection, and acceptance
permissions. Separately launched workers should use worker-scoped credentials.

Repeating the command resumes the same immutable campaign. Accepted tasks do not
run again; existing submissions can complete admission using their evaluator
receipts. Lease fencing rejects stale worker submissions. Attempt budgets persist
in the queue. Round limits apply to each invocation, allowing an unchanged campaign
to continue after a bounded run. Changing the campaign, driver command, goal, or
prepared contracts requires a new campaign/workspace rather than silently changing
work that other agents have already received.

The coordinator can synthesize dependencies for an open submitted constructor.
Only its exact staged root is eligible; independently selected dependencies must
come from the accepted repository. A staged dependency cannot bypass acceptance
by being selected alongside another submission. Ambiguous or unsatisfied dependency
selection remains a reported evaluator error for the integrator to resolve.

## Evidence and completion

The evaluator builds a locked environment, executes the authored acceptance cases,
signs its own observations, publishes the exact evaluated root, and acknowledges
the queue. It never signs a worker's claim that tests passed. A durable evaluator
receipt permits replay after publication succeeds but queue acknowledgement fails.
Receipt signatures and their environment/evidence bindings are checked again
before retrying publication.

A goal can require trusted evidence:

```toml
schema_version = 1
[goal]
name = "knowledge-base"
requires = { id = "knowledge.system", version = "1" }
capabilities = ["citation-preserving-retrieval"]
[policy]
allowed_effects = ["sqlite"]
[evidence]
tasks = ["<sha256-of-prepared-system-acceptance-contract>"]
evaluators = ["knowledge-evaluator"]
```

The hash comes from the prepared contribution task. Evidence is matched against
the complete selected composition, not merely a provider name or a capability
label. The final system contract should exercise the intended composition when
its goal requires such evidence. Passing isolated component evaluations does not
implicitly satisfy a whole-system evidence requirement.

The work directory contains:

- `campaign.json`: immutable campaign/goal/driver and prepared-contract identities.
- `contracts/`: prepared task bundles.
- `workers/`: driver outputs and attempt feedback.
- `evaluations/`: locked environments, reports, publications, and durable receipts.
- `resolution.json`: final synthesis result and diagnostics.
- `program.lock.json`: written only for a completed campaign with a unique goal.
- `report.json`: task states, attempt counts, and at most the latest 100 events.

The result is `complete` only when every declared task is accepted and the final
goal resolves uniquely. `needs-contributions` means all declared tasks passed but
the goal still lacks a unique valid selection; inspect the resolution before
authoring more work. `blocked` identifies an exhausted task budget. `round-limit`
means bounded execution ended while work remained. CLI exit status is zero only
for `complete`.

The real integration test builds two independent contributions: an identity
provider and an open increment constructor whose input has the same interface as
its output. The second task waits for the first's acceptance, the evaluator binds
only the exact proposed root, and the final goal requires signed evidence for the
composed program. The resulting lock contains both independently published modules.

```sh
.venv/bin/pytest -q tests/test_campaigns.py
```
