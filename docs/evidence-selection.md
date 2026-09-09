# Selecting compositions using evaluator evidence

A signature establishes compatibility. It does not establish that a knowledge-base
retriever finds the right document or preserves its citation. A contribution task
adds executable cases; a trusted evaluator attests the observation against the
exact task, composition, and Python environment that it evaluated.

Evidence selection is opt-in. Existing goals without an evidence policy continue
to select by declared interfaces, types, effects, and capabilities.

## Ask for evidence of a named contract

```toml
schema_version = 1

[goal]
name = "citation-preserving-search"
requires = { id = "kb.search", version = "1" }
capabilities = ["citations"]

[evidence]
# Replace this illustrative digest with prepare_contribution(...)["sha256"].
tasks = ["1111111111111111111111111111111111111111111111111111111111111111"]
evaluators = ["knowledge-ci"]
```

The task hash seals the interface and acceptance cases. Reusing a friendly task
name with different expected citations produces a different requirement. Every
required task needs a passing observation from a locally trusted evaluator. An
optional `evaluators` list narrows the trusted identities allowed by this goal.
All required tasks must have observations in at least one common executable
Python environment.

## Evaluate and attest

The evaluator controls this operation and keeps its key outside candidate code:

```python
import json
from pathlib import Path

from module_families.contributions import evaluate_contribution
from module_families.evidence import EvidenceStore, ingest_evidence

# Load a private random key of at least 32 bytes from evaluator-owned storage.
secret = Path("/private/evaluator.key").read_bytes()
store = EvidenceStore("evidence.sqlite")
report = evaluate_contribution("task.json", "environment.lock.json", "candidate-venv")
Path("observation.json").write_text(json.dumps(report))
attestation = ingest_evidence(
    "task.json", "environment.lock.json", "observation.json",
    store, "knowledge-ci", secret,
)
```

`ingest_evidence` verifies the sealed task, complete environment, selected
composition, exact case results, and report hashes before signing. Failed reports
are rejected. It signs an evaluator's acceptance of an observation. It cannot
determine whether an arbitrary uploaded unsigned report describes an execution
that actually happened. Do not expose the signing operation as an unauthenticated
upload endpoint. The evaluator must own the evaluation-to-attestation path or
explicitly trust the report it signs.

The store retains no secret keys. Observations are keyed by immutable content
hashes in SQLite, indexed by composition context and task. Insertion verifies the
signature; lookup verifies it again against current local trust keys. Removing a
key from local trust therefore stops its observations satisfying future searches.
Stored artifacts and observations remain immutable.

## Synthesize and seal

```python
from module_families.assemblies import lock_assembly, verify_assembly
from module_families.synthesis import synthesize

trust_keys = {"knowledge-ci": secret}
result = synthesize(
    "goal.toml", repository,
    evidence_store=store,
    trust_keys=trust_keys,
)
lock = lock_assembly(result, repository, trust_keys=trust_keys)
verify_assembly(lock, repository, trust_keys=trust_keys)
```

Search filters complete candidate expressions. It fails closed when an evidence
policy has no configured store/trust keys, when a signature is invalid, when the
evaluator is untrusted, or when no observation covers the selected context.
Rejected choices carry diagnostic reasons. This is an acceptance filter, not a
learned ranking function or an assertion that the best possible module was found.

The assembly lock preserves the policy and accepted attestation records. Locking
requires explicit trust keys and reauthenticates the records against the newly
locked composition. `verify_assembly(..., trust_keys=...)` authenticates them again.
Offline `verify_assembly(lock)` without keys only checks internal integrity and
coverage; its returned evidence result explicitly says `authenticated: false`.
An untrusted party can rewrite a lock and recompute ordinary hashes. Consumers
must authenticate with their own trust keys or obtain the lock through a trusted
channel. Trust keys never become part of the lock.

## Why context matters

Suppose a retry constructor passes the citation test when its child is a local
retriever. The same constructor attached to a different retriever is a different
program. Its previous passing result cannot authorize the new composition.

The context fingerprint binds:

- The entire nested expression and named dependency connections.
- Every selected member's immutable metadata, including associated type bindings.
- Every member's published artifact dependency closure and external requirements.
- The complete sealed interface declarations and any type-library bindings.

Local binding aliases and project names do not change identity. Changing a child,
a wheel hash, an embedding-space declaration, a dependency requirement, or an
interface does. This conservative exact matching intentionally sacrifices some
reuse of observations to avoid generalizing a result beyond what was evaluated.

Attestations also bind the original program and environment lock hashes. A
separate environment fingerprint binds the interpreter, resolved wheels, and
module runtime while excluding assembly receipt metadata. Adding a receipt does
not change the executable environment identity. Resolving a different dependency
wheel or using a different interpreter requires new matching evidence. Environment
verification checks that every required task has an observation in the actual
resolved environment.

## What this establishes

Evaluators can use Ed25519 with `module-families[signing]` installed. A
`PrivateEvaluatorKey` signs; a `PublicEvaluatorKey` verifies without granting
signing authority. Public attestations use their own signed format identifier,
and a public key object cannot be interpreted as an HMAC secret. This protocol
separation prevents accepting a forgery signed with publicly available key bytes.
The CLI accepts `{ "ed25519": "PUBLIC_KEY_HEX" }` trust entries and
`ed25519:PRIVATE_SEED_HEX` evaluator secrets. Private seeds must contain exactly
32 bytes and are excluded from the key object's representation.

HMAC-SHA256 remains available for explicit shared-secret trust domains. Anyone
holding an HMAC verification key can also sign. Ed25519 solves that authority
separation; it does not establish how an evaluator's public identity is enrolled,
rotated, revoked, or authorized across organizations. Trust configuration remains
an operator responsibility.

Evidence supports finite, artifact-bound observations. It is not a behavioral
proof, a Python sandbox, a guarantee of external service availability, or a
numerical quality optimizer. A compromised trusted evaluator can attest false
results. Candidate code must run under isolation appropriate to the evaluator's
threat model; a virtual environment isolates dependencies, not host privileges.

## Public evaluation identities

Install `module-families[signing]` to use Ed25519 attestations across independent
operators. The implementation uses the maintained
[cryptography Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).
The evaluator retains a `PrivateEvaluatorKey`; consumers receive only its
`PublicEvaluatorKey`. A verification key cannot sign observations.

```python
from module_families.signing import PrivateEvaluatorKey
from module_families.evidence import verify_attestation

private = PrivateEvaluatorKey.generate()  # Persist securely at the evaluator.
public = private.public_key()              # Distribute through trusted policy.
attestation = ingest_evidence(
    "task.json", "environment.lock.json", "observation.json",
    store, "knowledge-ci", private,
)
verify_attestation(attestation, {"knowledge-ci": public})
```

Consumer `MF_EVIDENCE_KEYS` accepts
`{"knowledge-ci":{"ed25519":"<32-byte-public-key-in-hex>"}}`.

The signed format identifies Ed25519 separately from HMAC. Verification rejects
algorithm/key-kind mismatches, including attempts to treat a public key as an
HMAC secret. Removing a public key from local trust invalidates its observations
for future selection. This is explicit local trust policy, not a global
certificate authority, transparency log, or retrospective recall of deployed
programs. Both protocols authenticate claims; evaluator isolation and meaningful
acceptance cases remain necessary.


## Candidate process credentials

Candidate-capable Python subprocesses receive a clean environment with a system
`PATH` and temporary home, working, and temporary directories. Evaluator secrets,
custom signing variables, cloud credentials, SSH agent sockets, and Python import
path overrides are not inherited. This also applies to interpreter fingerprint
checks and installed-environment checks, because a wheel's `.pth` startup code
can execute before a requested `-c` or `-m` command. Wheel-only dependency
downloads still use the operator's configured registry access in the trusted
host interpreter.

This is credential hygiene, not an operating-system sandbox. Candidate code
still runs as the evaluator's user and can attempt to read host files, inspect
processes, open network connections, or spawn children. A hostile contributor
requires an external container, VM, or equivalent process isolation policy with
separate credentials. The current local evaluator must not be presented as safe
execution of arbitrary hostile submissions. External-service secrets are not
implicitly passed into candidate programs; an explicit resource-grant mechanism
would need its own design and enforcement.
