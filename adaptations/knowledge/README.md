# Knowledge-system candidate bindings

This example adapts existing software to module ports. Its purpose is to exercise the general graph builder, not to define a new retrieval framework.

| Component | Existing implementation | Adaptation |
| --- | --- | --- |
| Diversification | Copied Mari `retrieval/fusion.py:maximal_marginal_relevance` | Build the original definition and its shared dataclass dependency unchanged. |
| Embedding | scikit-learn 1.8.0 `HashingVectorizer` | Fix 1,024 unsigned lexical features with L2 normalization; expose `embed(texts)`. |
| Embedding cache | Python `functools.lru_cache` | Cache 256 per-text vectors around a supplied embedding port. |
| Data layer | Python `sqlite3` | Adapt an in-memory connection to `load`, `read`, and explicit `close`; guard concurrent access. |
| Query execution | Python `ThreadPoolExecutor` | Execute at most four query workers, sharing the selected retrieval/store module. |

Mari source and licenses come from the preserved copy at `projects/mari-kit`. The compiler emits the selected source closure, and the real example records the source hash before and after execution. It never changes the original `~/mari-kit`. Scikit-learn and its transitive dependencies remain ordinary upstream wheels; environment locking records exact versions and hashes. Generated graph wheels reuse their dependency wheels exactly rather than relabeling ownership.

The code in [providers.py](src/knowledge_adapters/providers.py) is necessary boundary scaffolding: representation conversion, configuration, resources, and calls to supplied modules. MMR selection is Mari's original implementation. Vectorization is scikit-learn's original implementation. The [official HashingVectorizer reference](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.HashingVectorizer.html) describes its stateless feature-hashing approach and potential collisions.

The dataset is a small deterministic integration fixture, not a retrieval benchmark. Lexical hashed vectors are not learned semantic embeddings. SQLite here is an in-memory store, not a transactional revision-store implementation for all of Mari. The query harness uses Python threads, not an LLM reasoning loop. Concurrent batches through the same harness are serialized; callers must not close the exported store during an active batch. No external service or credential is required.

Run [knowledge_system.py](../../examples/knowledge_system.py) using the [module build guide](../../docs/module-build.md). The open retrieval graph can be published without choosing either an embedding implementation or data layer; the final graph makes those choices at build time.
