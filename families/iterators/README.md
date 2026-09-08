# Independent iterator publishers in one family

Prepare the preserved source with `PYTHONPATH=src python scripts/prepare_candidates.py`. The primary publication manifests are:

| Manifest | Publisher | Published objects |
| --- | --- | --- |
| `interfaces.toml` | `interfaces` | `iterators.chunker@1`, `iterators.unique@1` |
| `more-itertools.toml` | `more-itertools` | `more-itertools.chunked@11.1.0`, `more-itertools.unique@11.1.0` |
| `boltons.toml` | `boltons` | `boltons.chunked@25.0.0`, `boltons.unique@25.0.0` |

All three publications share the same immutable `iterators` family header. The interface publisher owns its interface identities; each implementation publisher owns its member namespace. The two libraries' algorithms are preserved upstream implementations, backed by explicit source-projection recipes and installed behavior comparisons.

Publish the interface manifest first, followed by either or both providers. Each manifest goes through the ordinary build and publish commands. Together the provider builds contain four member wheels and seven supporting cell wheels. The interface publication needs no implementation wheels.

Both chunk providers accept the `chunk(items, size, /)` call shape and expose the `batch` capability; both unique providers accept `unique(items, /)` and expose `deduplicate`. Those interfaces declare call acceptance. The shared behavioral scope additionally requires non-string iterables, strictly positive integer batch sizes, and hashable items for deduplication. Runtime call-shape checks do not enforce these laws.

`family.toml` is an optional build exercise combining both source roots under publisher `integration`. Use a separate repository for that build. The current compiler includes source licensing material in cell wheels, so combining sources can change packaging bytes under existing cell distribution identities. It is not the primary independent-publisher demonstration.
