# Associated types in an existing knowledge system

This example adds contracts to the existing Mari, scikit-learn, SQLite, and Python executor integration. It does not implement another retrieval algorithm. The build layer carries the relationships that a plain Python call signature cannot express.

Run from the repository root:

```sh
python examples/associated_system.py --work-dir .mf/associated-demo
```

For an offline dependency build, supply a wheelhouse containing scikit-learn 1.8.0, NumPy, their dependencies, and packaging:

```sh
python examples/associated_system.py \
  --work-dir .mf/associated-demo \
  --wheelhouse /path/to/wheels
```

The work directory must be empty. The script publishes the `associated-knowledge` family into a private local repository, builds an open retrieval module, closes it into a complete system, synthesizes a program, locks its environment, and executes it in a separate virtual environment. The top results for the two queries are `dogs` and `python`.

## Relationships checked before execution

| Module | Associated exports | Relationship |
| --- | --- | --- |
| Hashing embedding | `Space : identity` | Concrete identity describes this vector configuration |
| Cached embedding | `Space : identity` | Same space as its `base` dependency |
| SQLite document store | `DocumentId : shared` | Identity names a document namespace |
| Retrieval | `Space`, `DocumentId` | Taken from its embedding and data dependencies |
| Threaded harness | `DocumentId` | Retrieval and ingestion data must agree on document identity |
| Complete system | `DocumentId` | Exposes the selected data namespace |

The embedding interface describes its result as `VectorBatch[Space]`. `Space` is an abstract interface parameter; it becomes the selected provider's concrete witness during composition. Two providers with identical `embed(texts)` Python call shapes therefore cannot silently substitute different vector spaces in a typed export.

The cached embedding and open retrieval modules carry dependency projections, such as:

```toml
[associated.types.Space]
from = "embedding.Space"
kind = "identity"

[associated.types.DocumentId]
from = "data.DocumentId"
kind = "shared"
```

The harness declares an equation between the document identities of two dependencies. This equation survives publication and is checked when the surrounding graph supplies those dependencies. The harness author does not need to know which document namespace the integrator will select.

## Two deliberate failures

The example checks both failures before executing the valid system:

1. **Conflicting vector witness.** A fixture reuses the existing hashing implementation but declares a contradictory `Space`. A graph that exports its typed `embed` operation while promising the original space is rejected. The diagnostic identifies a typed export contract mismatch.
2. **Conflicting document namespace.** A fixture reuses the SQLite adapter but declares another `DocumentId`. Connecting it to the harness while retrieval uses the original namespace violates the harness's sharing equation. This negative graph removes `same_instance`, so rejection specifically demonstrates the associated type constraint.

These are deliberately inconsistent metadata fixtures, not claims to have implemented two different embedding algorithms or independent storage systems. They demonstrate rejection of conflicting declarations, not verification that Python implementations tell the truth.

The valid system retains `same_instance`: two stores can use the same document identifier type and still hold different data. Type equality and object identity solve different problems.

## Inspecting the result

The generated `manifests/` directory contains the complete TOML authoring inputs, including both rejected graphs. `build/` contains generated constructors and publication artifacts. `program.lock.json`, `environment/`, and `report.json` record the selected artifacts, reproducible execution environment, result, and rejection diagnostics.

The source declarations are derived from [the existing knowledge family](../../families/knowledge/) and [module graphs](../../examples/modules/). Source and license paths are made absolute when relocated into the work directory. The script verifies that the copied Mari implementation's hash remains unchanged.

The Python boundary remains trusted. HashingVectorizer supplies lexical features, the harness is a ThreadPoolExecutor, and SQLite is an in-memory store. This example demonstrates first-order associated type substitution and modular sharing constraints. It does not demonstrate generative type identities, generic `.mfl` programs, or a proof of numerical correctness.
