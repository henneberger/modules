# Local registry and module locks

`module_families.registry.Registry` provides an import-free repository for any module family. The registry uses SQLite with FTS5 and a SHA-256 content store; PEP 440 version handling uses `packaging`. [The HTTP repository service](repository.md) exposes the same consumer operations remotely.

```python
from pathlib import Path
from module_families.registry import Registry

registry = Registry(Path(".registry"))
registry.publish(Path("dist/index.json"))
candidates = registry.search("rank documents", family="mari", limit=5)
card = registry.inspect("mari", candidates[0]["id"])
lock = registry.lock("mari", card["id"], card["version"], allowed_effects=[])
registry.materialize(lock, Path(".installed"))
registry.export_simple(Path("simple"))
```

## Publication identity

The input is a build index with wheels adjacent to `index.json`. Every index requires a `publisher` identity, and each member requires `publisher`, `local_id`, and `id` equal to `publisher + "." + local_id`. A publication may contain any independently releasable subset of a family's members, provided it includes the complete internal dependency closure of all included artifacts. Families are open contribution contexts: Alice and Bob can publish `alice.solve` and `bob.solve` under the same existing family header, without transferring ownership or obtaining the first contributor's permission.

- A family header is immutable under `(family.name, family.version)`.
- A member is immutable under `(family.name, member.id, member.version)`.
- A distribution artifact is immutable under `(normalized_distribution, artifact.version)`.
- The first publisher of a distribution controls new versions of that distribution. Other publishers can reuse its exact existing artifact records and bytes as dependencies.
- Interface IDs belong to their first publishing principal across families; each `(interface.id, interface.version)` specification is immutable.
- `member.version` defaults to the family context version when omitted. It may advance independently. Published cards and locks preserve both `version` and `family_version`.
- Repeating the same publication is idempotent. Adding another member under an existing family header is allowed.

The complete index is stored once as an immutable publication snapshot, referenced by its member rows. This makes the exact artifact graph available without duplicating it for every member. CAS blobs are written before the SQLite transaction commits; the catalog never exposes a partial publication. An interrupted write can leave an unreferenced blob, which is harmless but is not automatically garbage-collected.

The registry checks dependency existence, cycles, exact internal `Requires-Dist` version pins, member-to-wheel mappings, filenames, hashes, wheel metadata identities, and wheel `RECORD` entries. Search, inspect, publish, and lock never import candidate code. A member's declared `import_module` must be physically provided by its wheel.

An index can publish only `interfaces`, with no members or artifacts. Interface specifications are validated and canonicalized using the declarative signature schema. Interface conflicts roll back the entire publication, including new members and distributions. `interface(id, version)` returns one exact specification; `interfaces(limit=100, offset=0)` lists them. The current database schema is version 3. Earlier prototype databases are rejected with a request to create a fresh repository; there is no migration or unscoped publication mode.

## Discovery and versions

`search(query, *, family=None, contract=None, allowed_effects=None, limit=10, offset=0)` returns member cards. Query words become quoted FTS5 tokens, preventing user input from becoming FTS query syntax. The search includes family context as well as member metadata. Family and contract filters are exact; effect filters are applied before pagination. Each member appears at its most recently published version.

`inspect(family, member, version=None)` returns one card. Omitting the version chooses the most recent publication, not a guessed ordering of Python versions. `versions(family, member)` returns all cards in reverse publication order. `families(*, limit=100, offset=0)` returns immutable family headers, including their context versions. Search and family-page sizes are limited to 1,000.

`candidates(contract_id, contract_version, *, family=None, version_spec=None, limit=100, offset=0)` enumerates exact `provides.id` and `provides.version` matches across all immutable releases. It applies a PEP 440 specifier before pagination, includes matching prereleases, and orders versions newest first with deterministic family/member ties. This supplies finite repository candidates to dependency resolution; it does not choose a provider by text-search rank. The indexed exact lookup still sorts matching versions in memory.

Effects are publisher declarations, not runtime capabilities. With no `allowed_effects` filter, discovery allows any declaration. With a filter, missing, malformed, `unknown`, `unspecified`, and wildcard effect declarations are rejected, even if the allow-list contains those labels. An empty explicit effect list satisfies an empty allow-list. This is a metadata policy and supplies no sandbox guarantee.

## Exact module locks

`lock(family, member, version=None, allowed_effects=None)` returns a JSON object with:

- `schema_version: 1` and `format: "module-families-lock"`;
- family, member, member version, and family context version;
- the full selected member card;
- a sorted exact transitive closure of artifact records;
- unresolved external Python requirements;
- optional declared effect policy;
- `environment.locked: false` and an explanatory reason;
- `sha256`, computed over the remaining object using UTF-8 JSON, sorted keys, no insignificant whitespace, and no non-finite numbers.

There are no generated registry filesystem paths in a lock. A lock can replay using a copy of the repository. Before materialization, the registry regenerates the expected lock from its stored member snapshot and compares the complete document, then verifies artifact records and wheel bytes. Removing dependencies or changing an entry point fails even if the edited lock's digest is recomputed.

This is a module selection lock. It is not a `pylock.toml` environment lock and does not resolve, fetch, or install external distributions or the interpreter. External requirements are returned explicitly. Digest verification establishes byte identity relative to stored records, not publisher authenticity or algorithm correctness.

## Materialization and standard installers

`materialize(lock, target)` writes a selected closure into a Python site-directory layout. It validates all files and existing destinations before writing. Identical preexisting files are allowed for repeat replay; conflicting files are rejected. Multiple distributions cannot own the same file. Traversal, absolute archive paths, archive symlinks, special files, and symlinks within the destination are rejected.

This reference materializer supports pure Python wheels (`Root-Is-Purelib: true`) without `.data` installation schemes. It verifies SHA-256/384/512 `RECORD` hashes, limits expanded file sizes, and rejects unsupported wheel layouts. It does not register a complete environment, create console-script wrappers, compile bytecode, resolve dependencies, enforce CPU/memory/network limits on later execution, or provide transaction isolation against adversarial concurrent filesystem writers. An I/O failure during writing can leave a partially materialized destination; replay with the same lock accepts matching files and completes it.

`export_simple(target)` emits an HTML Simple Repository index, wheel files with SHA-256 links, and detached core metadata with its hash. The target must be new or already contain identical export files; use a new directory to export a changed catalog. Existing Python installers can consume this index. A configured HTTP server can serve it, but this method itself does not start a server or upload anything.

Direct filesystem access is trusted local administration. The HTTP service authenticates publisher principals and enforces their qualified member namespaces. Neither path implements artifact signatures, attestation verification, revocation, freshness/rollback protection, or federated consistency.

## Verification

Run `PYTHONPATH=src python3 -m unittest discover -s tests -p test_registry.py -v`.

The suite checks isolated selected closures, external dependency boundaries, portable locks, independent member publications and versions, immutable records, missing/cyclic/hidden dependencies, import-free filtered discovery, artifact/lock/RECORD tampering, path and symlink rejection, destination conflicts, and standard-index export. It does not constitute a global-scale performance measurement.
