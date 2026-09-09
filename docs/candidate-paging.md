# Bounded candidate discovery

Candidate lookup now decodes only the requested page of member cards. This matters
when one module family contains hundreds of thousands of independently published
algorithms: asking for ten potential rankers should not construct a Python object
for every ranker in the repository.

Previously, `Registry.candidates()` loaded all matching JSON cards, parsed their
versions, sorted the entire collection in Python, and then sliced it. A federation
could request bounded pages while each underlying shard still repeated that
family-sized allocation. That behavior has been removed.

## How the query works

SQLite connections register a PEP 440 version collation backed by `packaging.Version`.
A bounded cache retains at most 4,096 parsed version strings. Two indexes support
contract-wide and family-filtered queries in descending PEP 440 order, followed by
the existing family, qualified member ID, and raw version tie-breakers. Versions
such as `1.0` and `1.0.0` retain their prior semantic ordering and deterministic ties.

A version predicate, when present, runs against scalar version strings before SQL
`LIMIT` and `OFFSET`. JSON decoding happens only on the returned rows. A zero-sized
page validates its arguments and version predicate, then returns without opening
a database connection.

Regression tests compare paginated results with the previous ordering semantics
across epochs, prereleases, development releases, postreleases, local versions,
equivalent spellings, exact and wildcard predicates, and family filters. A
10,000-row test requests one card at offset 9,000 and observes exactly one JSON
decode. Query-plan tests require an ordered candidate index and no temporary sort.

## What remains proportional to catalog size

A small page now bounds Python card allocation, not every kind of query work.
A deep offset still traverses preceding index entries. An arbitrary PEP 440
specifier that matches nothing can inspect every version in the contract domain.
Those queries do not decode skipped JSON cards, but their elapsed time can grow
with the domain. Narrow contract/family filters and avoid repeatedly walking deep
offsets when possible.

The indexes move ordering work into publication and consume disk space. Existing
catalogs create the additional indexes when opened; building them over a large
existing catalog is a one-time ordering cost. This is an index addition, not a new
card format or a second full metadata snapshot.

## Recorded local results

These single local runs used Python 3.14.6 on macOS 26.5.1 arm64. Sixteen version
strings were distributed across the synthetic member rows.

| Query | 100,000 rows | 1,000,000 rows | Decoded cards | Peak traced Python allocation |
|---|---:|---:|---:|---:|
| First result | 0.28 ms | 0.90 ms | 1 | 4,536 bytes |
| First 100 results | 1.03 ms | 4.20 ms | 100 | 128,762 bytes |
| Last result using a deep offset | 4.94 ms | 194.02 ms | 1 | 5,062 bytes |
| Unmatched version predicate | 117.38 ms | 1,202.55 ms | 0 | about 3,550 bytes |

The one-million-row database occupied 666,025,984 bytes, including card text and
indexes. Synthetic insertion took 22.99 seconds. These observations support bounded
card decoding and expose the remaining scan cost; they are not latency guarantees.

## Reproduce the measurement

```sh
python scripts/benchmark_candidate_paging.py --members 100000 \
  --work-dir .mf/candidate-paging-100k-reproduction

python scripts/benchmark_candidate_paging.py --members 1000000 \
  --work-dir .mf/candidate-paging-1m-reproduction
```

The script inserts synthetic catalog rows in bounded batches, then measures four
queries. It intentionally bypasses wheel publication: these are candidate-query
measurements, not claims about a million validated publications or active agents.
`tracemalloc` reports Python allocations during each query; native SQLite caches
and total process resident memory are outside that figure. Each work directory
contains its SQLite database and one bounded summary JSON file.

Measured values are recorded in [the validation summaries](candidate-paging-validation.json).
