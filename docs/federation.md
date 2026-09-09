# Federated repository discovery

`FederatedRegistry` composes independently operated filesystem and HTTP
repositories into one read interface. A project can discover an embedding provider
from one publisher's repository, a search index from another, and a checked
knowledge-base constructor from a third. Existing synthesis, graph checking,
locking, and artifact materialization consume that interface.

The adapter does not copy publications into a combined index. It streams bounded
pages from a fixed, named shard set and downloads only selected wheel objects.
Publishers keep their own repositories; publication goes directly to the chosen
repository, never through an ambiguous federation-wide write route.

## Author a federation in TOML

```toml
schema_version = 1
page_size = 100
max_scan = 100000

[shards.contracts]
location = "./repositories/contracts"

[shards.embedding]
location = "https://embedding.example.org"

[shards.search]
location = "./repositories/search"
```

Save this as `knowledge.federation.toml`. Relative paths resolve against that
manifest's directory. HTTP repositories use their existing public read API; tokens
are not embedded in this configuration. List one to 32 concrete shards. Nested
federation manifests are rejected so recursion cannot conceal unbounded fan-out.

```python
from module_families.repository import open_repository
from module_families.synthesis import synthesize
from module_families.assemblies import lock_assembly, instantiate

repository = open_repository("knowledge.federation.toml")
result = synthesize("knowledge-goal.toml", repository)
lock = lock_assembly(result, repository)
knowledge = instantiate(lock, repository, ".mf/knowledge-instance")
```

`knowledge-goal.toml` must name contracts and capabilities published by the
configured repositories. The ordinary synthesis rules still apply: the result
must satisfy its bounds and selection policy, interfaces and associated types
must compose, and declared effects must fit the requested policy. The federation
is a discovery adapter, not an exception to those checks.

For programmatic construction, pass already opened repositories:

```python
from module_families.federation import FederatedRegistry
from module_families.registry import Registry

repository = FederatedRegistry({
    "contracts": Registry("repositories/contracts"),
    "contributors": Registry("repositories/contributors"),
}, page_size=100, max_scan=100000)
```

A regression test publishes a client and its interface to one repository and a
retry constructor to another. Synthesis discovers the constructor, fills its
client dependency across the boundary, locks both artifact closures, materializes
them, and executes the composed Python program. Another test serves a shard over
HTTP and verifies that unselected wheels are never downloaded.

## Identity and collision rules

A qualified member release is identified by family, publisher-qualified member ID,
and exact version. Its canonical card must agree across every shard that holds
that identity. Exact selection checks all shards, including shards whose conflicting
card advertises a different interface and therefore would not appear in the same
filtered candidate query.

Interface identity is its ID and version. Conflicting specifications are rejected
before candidate selection. Result and dependency interfaces are also checked when
locking a member directly. An interface may exist in a contract repository without
being copied into every provider repository.

Identical mirrors collapse to one result. A conflicting family header or interface
encountered during page merging fails the request. Selected member mirrors must
also agree on the complete artifact closure, not just the top-level wheel hash.
Across selected closures, a distribution/version pair cannot bind conflicting
artifact metadata. Downloaded object hashes are verified again before installation.
The adapter does not scan all unselected artifacts across every repository.

Repository failure is not interpreted as absence. An unavailable or malformed
shard fails the operation instead of making synthesis silently select from a
smaller ecosystem. This conservative behavior prevents an outage from hiding an
immutable identity collision. There is no fallback that trusts a conflicting mirror.

## Ordering, bounds, and snapshots

Candidate pages use descending PEP 440 version order, then family, qualified member
ID, and version text. Family headers and interfaces use their lexical identity
order. A heap merge retains at most a page per shard plus the requested output,
without accumulating the entire repository. Each shard must preserve the expected
strict ordering; violations are rejected.

`limit` is at most 1,000. `page_size` is 1–1,000. `max_scan` bounds records traversed
while satisfying a paginated query, including duplicate mirrors and skipped
prefixes. Exceeding the bound raises an explicit error. Narrow the family or
version query rather than treating a truncated search as complete.

The older `versions(family, member)` repository API returns one unpaginated member
history. Federation bounds the combined history it accepts, but cannot prevent an
underlying local implementation from allocating that history first. HTTP response
limits still apply. Candidate discovery is the preferred paginated path for large
histories.

Text search preserves named-shard order and each shard's own ranking. It does not
pretend that independently computed BM25 scores are globally comparable. Use
candidate discovery and synthesis for exhaustive typed selection within declared
bounds. `inspect` without a version selects the highest PEP 440 version across
shards; this is deterministic and does not compare unrelated publication clocks.

Pagination is deterministic for an unchanged shard set. Each repository exposes
an append-only publication revision through `revision()` and `/v1/revision`;
federation returns a vector over its named shards. Synthesis records the revision
before discovery and checks it again before reporting completeness. A change
produces an `incomplete` result with `repository_changed`; it cannot become an
automatically selected lock or an absence-based contribution handoff. Rerun the
search against the updated catalog.

This is an optimistic stability check, not a retained snapshot or a cross-shard
transaction. It conservatively rejects even unrelated publication during search.
Constant publication can cause repeated retries. Snapshot retention or stable
cursors would be needed to guarantee search progress under uninterrupted writes.
Direct callers that combine pages themselves must compare revisions too. Use
immutable locks to retain a program selected from a stable view.

## What this contributes to scale

Independent teams can own storage and publish contracts or implementations without
centralizing every card into one enormous manifest. The build system selects and
materializes the small closure needed for a particular program. Exact type
relationships and collision checks retain meaning across those storage boundaries.

This is bounded federated discovery, not distributed consensus, high-availability
replication, global publisher governance, or evidence that millions of agents can
successfully coordinate. Query fan-out and exact collision checks still grow with
the configured shard count. Routing directories, authenticated immutable publisher
identities, cross-shard snapshots, and larger deployment benchmarks require further
work before expanding beyond the explicit 32-shard bound.


Candidate pages are bounded within each local shard, not just in the federation
response: SQL indexes supply PEP 440 ordering and only selected card bodies are
decoded. See [candidate paging](candidate-paging.md) for million-row measurements,
deep-offset costs, and arbitrary version-filter scan limits.
