# Measuring coordination scale

The repository now has a durable task queue, leased workers, fenced submissions,
and exact-submission acceptance. The benchmark exercises those real queue methods
against a large backlog. It does not run a million agents or demonstrate that a
million independently written implementations form a useful program.

## Recorded local measurements before readiness indexing

These runs used Python 3.14.6, SQLite 3.53.4, and macOS 26.5.1 on arm64. Each task
contains a 122-byte synthetic payload referring to a shared contract digest. Task
identities are unique. Payload text is stored separately in every row; the queue
does not deduplicate that text. One additional record exercises lease recovery.

| Measurement | 100,000 task records | 1,000,000 task records |
|---|---:|---:|
| Worker processes / identities | 4 / 4 | 8 / 8 |
| Completed claim → submit → accept workflows | 4,000 | 16,000 |
| Enqueue time, batches of 1,000 | 1.62 s | 16.82 s |
| Enqueue throughput | 61,569 records/s | 59,443 records/s |
| Completion time, including process startup | 3.18 s | 10.60 s |
| Completion throughput | 1,260 workflows/s | 1,509 workflows/s |
| Median claim latency | 0.194 ms | 0.195 ms |
| 99th percentile claim latency | 1.78 ms | 12.05 ms |
| 99th percentile full workflow latency | 27.69 ms | 76.80 ms |
| Maximum full workflow latency | 489 ms | 2,085 ms |
| Database size after checkpoint | 35,545,088 bytes | 353,087,488 bytes |

[Raw bounded summaries](coordination-scale-validation.json) preserve the measured
values and workload limits. These are individual local runs, not confidence
intervals or service-level guarantees. No per-task JSON export is generated.

Both runs abandoned a real short lease, reclaimed the same task under a larger
fence number, rejected the expired worker's submission, then accepted the replacement
worker's exact submission digest. This tests expiry and fencing. It does not inject
a process kill, database corruption, machine failure, or network partition.

## What this establishes

A million pending task records do not require a million active processes. The
large backlog remains compact enough for one local database, and indexed selection
can find an immediately eligible task without reading the whole backlog. Bounded
batch insertion makes preparing that backlog practical. A preliminary run using
1,000 individual `enqueue` transactions took 0.57 seconds; batching amortizes
connection and transaction overhead while preserving immutable task IDs and
all-or-nothing insertion. A batch can refer to earlier tasks in that same batch.

This is the coordination pattern a large project needs: persistent obligations
outlive workers, active workers claim a bounded amount of work, and a stale worker
cannot overwrite a replacement's submission. It is one part of agent collaboration.
Types, executable contracts, evaluation, artifact publication, and accountable
acceptance must still establish that the completed work is useful.

## Bottlenecks the measurements expose

SQLite permits one writer. Increasing workers from four to eight increased measured
throughput modestly while increasing tail latency. The runs have different backlog
and completion counts, so this is an indication of contention, not an isolated
causal experiment. The correct next load study holds the workload constant while
varying workers, transport, and disk characteristics.

The immediately eligible workload is favorable. A separate diagnostic placed
10,000 higher-priority tasks behind one unaccepted prerequisite. Finding each of
100 eligible lower-priority tasks took about 5.3 ms at the median in an initial
run, compared with roughly 0.2 ms in the unblocked runs. That original readiness query
checked candidate prerequisites; a large blocked frontier added work. The measured
problem is now addressed by an explicit readiness index, described below.

The payload is deliberately small. Large repeated task packets would multiply
storage and transfer costs. Store immutable contribution contracts and artifacts
once, then enqueue their digests and bounded task-specific context. This benchmark
uses that reference pattern but does not provide a general content-deduplicated
payload store.

A geographically distributed deployment needs partitioning, replication, recovery,
and explicit cross-shard dependency semantics. Neither a million records nor these
throughput numbers demonstrate those properties. HTTP authentication exists in the
coordination service, but this benchmark calls the local SQLite API directly. It
excludes model inference, network transport, artifact transfer, sandbox execution,
conformance evaluation, and human review.

## Indexed readiness: implementation and before/after evidence

Each task now stores its number of unaccepted prerequisites. Enqueue calculates
that count inside the same write transaction that inserts the task. Acceptance
atomically decrements direct dependents through a reverse dependency index. An
idempotent acceptance does not decrement twice. A task enters the partial ready
index only when it is pending and its count is zero. Claim reads that ordered
index directly, preserving priority, creation time, and task ID ordering.

Rejection, submission, lease expiry, and retry do not release dependents. Only an
accepted prerequisite does. Accepted states are irreversible, and dependency lists
are immutable, which makes the counter maintainable without a general graph
recalculation. Tests cover fan-in, fan-out, acceptance/enqueue races, reopening,
retry behavior, batch rollback, and use of the intended query plan.

The same diagnostic with 10,000 blocked high-priority tasks and 100 eligible
completions improved median claim time from approximately **5.3 ms to 0.67 ms**
after readiness indexing. Its 99th percentile after the change was **1.99 ms**.
These are single local measurements, not a speedup guarantee. The new query
explicitly selects the partial index: testing found SQLite could otherwise choose
the lease-expiry index and sort pending rows, defeating the intended optimization.

The current-schema 100,000-record rerun completed 4,000 workflows with four worker
processes at **1,337 workflows/s**, with a **34.7 MB** checkpointed database. The
baseline reports above remain as historical observations, and the validation JSON
includes the indexed reruns separately. The current-schema million-record rerun
completed 16,000 workflows with eight worker processes at **1,415 workflows/s**,
with a **343.3 MB** database. Enqueue took **17.72 seconds** and full-workflow
99th percentile latency was **105.31 ms**. The optimization targets blocked
frontiers; these eligible-only workloads do not show a general throughput win.

Acceptance now does work proportional to its direct dependent count. A prerequisite
with a million direct dependents can therefore hold the write transaction for a
long time even though later claims are fast. Sharding or bounded dependency-release
processing would need explicit atomicity semantics; this change does not claim
that those tradeoffs are solved.

This is a queue schema change. Old queue databases are rejected with an explicit
incompatible-schema message; there is no hidden migration or compatibility path.
Create a fresh queue when using this version. Recorded historical benchmark
artifacts remain readable through SQLite tooling.

## Reproduce

Install the project in the active Python environment, then use a fresh directory
for each run:

```sh
python scripts/benchmark_coordination.py --tasks 100000 --workers 4 \
  --completions 4000 --work-dir .mf/coordination-100k-reproduction

python scripts/benchmark_coordination.py --tasks 1000000 --workers 8 \
  --completions 16000 --work-dir .mf/coordination-1m-reproduction

python scripts/benchmark_coordination.py --tasks 100 --workers 1 \
  --completions 10 --blocked-tasks 10000 \
  --work-dir .mf/coordination-blocked-reproduction
```

The script emits one bounded `summary.json` and retains SQLite files in the work
directory. `--tasks` counts records; `--workers` sets the process pool and worker
identity count; `--completions` counts full accepted synthetic workflows. Acceptance
uses the real administrative method with the exact submission hash, but the result
is synthetic and is not an evaluation of contributed Python code. The default run
uses 10,000 records, four workers, and 1,000 completions.
