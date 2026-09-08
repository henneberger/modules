# Mari Kit conversion audit

Audit date: 2026-09-08. Source: `/Users/henneberger/mari-kit`, repository HEAD `36fb3a6026c2cb5f59f582d3fc3111d8b1f6920c`. The source was inspected without executing or modifying it. No applicable `AGENTS.md` was found in source or its filesystem ancestors. The repository was clean when inspected.

The first family should be named for Mari Kit while retaining the provenance that its Python distribution is **`mark-kit`** and its Python import package is **`mark_kit`**. The source audit found on 167 public implementation modules and 988 public top-level definitions. Those definitions are not all independent algorithms: 424 are functions and 564 are classes, including records, enums, protocols, adapters, and validators. The expanded source inventory is generated research output, retained locally under `.mf/legacy-generated/`; it is not an authoring file. The compact [family TOML](../families/mari/family.toml) controls current builds, and `plan-build` regenerates selected source relationships.

## Recommended publication boundary

Publish independently addressable public functions and operational classes, with separate support records and contracts. Build their payloads from the transitive closure of top-level source definitions, imports, and assignments. Merge mutually dependent definitions into one shared source unit. Use a stable shared runtime namespace so two algorithms consuming the same immutable record actually share one Python class object. Independently vendoring every algorithm's record classes would break `isinstance`, dataclass equality, and composition.

Each algorithm should have its own publication manifest, source digest, dependencies, exported signature, workload description, limitations, verification evidence, and discoverable family membership. The family is an index over those units, not a Python dependency that installs the entire library. Records and protocols should be labeled as support contracts; naming them algorithms would inflate the result and mislead agent selection.

Definition-level granularity is a useful automatic baseline, not a claim that a Python definition equals a research algorithm. `BM25VariantIndex` exposes three formulas through one enum; `maximize_subset` exposes four optimizers; `cohesive_subgraph` selects different graph operations. The family can additionally expose configured providers for those choices without copying their implementation. Conversely, a complex method may require several supporting definitions. Stateful algorithms should export the class as a unit, preserving initialization and method invariants.

The nine files in `mark_kit.algorithms` contain **35 public operational definitions**: 30 functions plus the BM25 index class and four subset objective classes. They also contain 35 public supporting classes and enums. They make a particularly clear first acceptance set:

| Source module | Independently useful operational exports |
| --- | --- |
| `algorithms.lexical` | `BM25VariantIndex` |
| `algorithms.subsets` | `FacilityLocation`, `ProbabilisticSetCover`, `SetCover`, `LogDeterminant`, `maximize_subset` |
| `algorithms.graphs` | `prize_collecting_forest`, `louvain_partition`, `hierarchical_leiden_partition`, `condense_graph`, `transitive_reduction_edges`, `cohesive_subgraph` |
| `algorithms.graph_retrieval` | `hipporag_seed_weights`, `weighted_chunk_polling`, `expand_typed_links`, `rank_graph_distances`, `rank_episode_mentions`, `rank_candidate_union` |
| `algorithms.search` | `drift_search`, `refine_extraction` |
| `algorithms.compression` | `select_surprising_words`, `fastcdc_chunks` |
| `algorithms.memory` | `memory_heat`, `lfu_evictions`, `heat_promotions`, `evolve_neighborhood`, `reduce_skill_feedback` |
| `algorithms.linkage` | `learn_blocking`, `acquire_disagreement`, `greedy_matching`, `gazette_matching`, `centroid_clusters` |
| `algorithms.temporal` | `recency_decay`, `dated_recency`, `temporal_proof_score` |

This is only the explicitly named `algorithms` directory. Many established algorithms live elsewhere: BM25, HNSW, IVF/PQ and dense/sparse indexes; MUVERA and MaxSim; reciprocal rank fusion and maximal marginal relevance; graph centrality, traversal, communities, and similarity; document sequence diff; statistical estimates; memory admission and consolidation; trajectory and process mining. A conversion claiming every algorithm must include the whole source inventory, not only those nine files.

## Source structure and import behavior

There are 181 Python source files: 167 public implementation files, 13 public facades, and one private helper (`connectors/_shared.py`). The source includes 188 public methods, 53 test files, and 29 example Python files. The repository's existing inventory generator, `mari-kit-landing/tools/generate_algorithm_inventory.py`, already uses AST inspection and distinguishes facades from implementations.

Package facades are eager. `mark_kit/__init__.py` imports shared types, contracts, aggregates, dependencies, grouping, and selection. The `retrieval`, `knowledge`, `graph`, `trajectories`, and other package initializers import broad sibling sets. There is no `__getattr__` lazy export architecture. The `algorithms` initializer is a docstring only, but Python still executes the root initializer before importing an algorithm submodule.

`documents/__init__.py` is an implementation module containing 17 public definitions. `governance/__init__.py` contains 24. Removing every initializer or replacing every initializer with an empty namespace would discard real implementations. An extraction compiler must resolve imports to owning definitions rather than treating every package initializer as a facade.

The following conservative file-level closure estimates include every static import, including those under functions and `TYPE_CHECKING`. They measure source coupling, not actual executed imports or mandatory dependencies.

| Entry source | Direct file import closure | Closure including eager Python parent packages |
| --- | --- | --- |
| `algorithms.lexical` | 1 file, 5,187 bytes | 13 files, 67,534 bytes |
| `algorithms.compression` | 1 file, 8,520 bytes | 13 files, 70,867 bytes |
| `retrieval.fusion` | 1 file, 5,718 bytes | 104 files, 685,790 bytes |
| `retrieval.maxsim` | 1 file, 1,550 bytes | 104 files, 685,790 bytes |
| `knowledge.excerpt` | 1 file, 2,621 bytes | 104 files, 685,790 bytes |
| `graph.centrality` | 1 file, 5,332 bytes | 104 files, 685,790 bytes |
| `documents.markdown` | 16 files, 118,959 bytes | 23 files, 162,734 bytes |

For example, reciprocal rank fusion is a small standard-library implementation, but importing its existing package path introduces broad source dependencies. Definition extraction can remove that accidental coupling while preserving the operation.

## AST extraction feasibility and constraints

Mari is unusually well suited to static extraction. Top-level statements across all source files are:

| Statement | Count |
| --- | ---: |
| Module docstrings | 181 |
| `from ... import ...` | 1,119 |
| `import ...` | 211 |
| Assignments | 169 |
| Annotated assignments | 2 |
| Classes, including private classes | 569 |
| Functions, including private functions | 611 |
| `__all__ += ...` | 2 |
| `if TYPE_CHECKING` | 1 |

No wildcard imports, non-docstring top-level expression statements, `exec`, `eval`, `globals()`, `locals()`, or `__import__` calls were found. The two augmented assignments extend the `knowledge` and `retrieval` facade export lists. The sole conditional imports `TrajectoryRun` for type checking in `knowledge/experience.py`.

A whole-source compiler is feasible if it:

1. Resolves module aliases, relative imports, and re-exports to their owning definitions, including definitions in initializers.
2. Includes bindings referenced by decorators, class bases, default argument expressions, bodies, and annotations. Assignments carry TypeVars, type aliases, regexes, configuration constants, and the FastCDC gear table.
3. Distinguishes local bindings, comprehensions, class scopes, and closure scopes from module globals. Conservative overinclusion is safer than silently dropping a dependency, but should be measurable.
4. Handles mutually recursive values and postponed or quoted annotations. A self-referential type alias exists in `types.JsonValue`; forward references also appear in dataclass fields.
5. Preserves ordering inside merged units when decorators and default values require earlier bindings.
6. Keeps optional imports inside their original functions and explicitly declares dynamically selected dependencies.
7. Reports unsupported syntax or unresolved bindings as build errors rather than publishing broken artifacts.

The source has seven nested import statements: a local `re` import in a connector, a local `MappingProxyType` import in JSON helpers, graph interchange's optional NetworkX/RDFLib/PyTorch/PyG imports, and the type-checking import. It has ordinary `type(value).__name__` / `__qualname__` uses, plus `__dict__` replacement in `DependencyIndex`; these do not depend on the old source module's namespace. Changing generated `__module__` paths will affect repr, pickling and exact introspection metadata, however. That compatibility boundary should be documented and tested where promised.

The audit does not prove that arbitrary Python libraries can be transformed this way. It identifies the constrained syntax and import behavior present in this source snapshot.

## Dependencies and caller-owned effects

The original package requires Python 3.11 or newer and declares only `numpy>=1.26` as a required dependency. Much of its functionality is standard-library only. NumPy appears in vector retrieval, subset objectives, graph adapters, graph candidate ranking, and trajectory clustering or sampling. File-level dependency declarations would unnecessarily pull NumPy into some operational definitions that happen to share a file with a vector operation; definition-level closure can distinguish those cases.

Optional solver imports are deliberately delayed:

| Boundary | Dependency |
| --- | --- |
| `algorithms.graphs._dependency` | Dynamic names `networkx`, `pcst_fast`, `graspologic_native` |
| `algorithms.linkage.centroid_clusters` | Dynamic constant `scipy.cluster.hierarchy` |
| Graph interchange to NetworkX | Function-local `networkx` |
| Graph interchange to RDF | Function-local `rdflib` |
| Graph interchange to PyG | Function-local `torch` and `torch_geometric` |

The `algorithm-solvers` extra declares NetworkX, pcst-fast, graspologic-native, and SciPy. The original project's extras do not declare RDFLib or PyTorch/PyG. A per-member registry needs explicit import-name-to-distribution metadata; `pcst_fast` is distributed as `pcst-fast` and `graspologic_native` as `graspologic-native`. Dynamic import helper dependencies must be traced from the selected caller or conservatively declared, since scanning only `import` statements cannot recover them.

Framework and service SDK integrations are caller-owned. The package exposes HTTP transport, generation, embedding, validation, storage, and other callback boundaries. It does not import common host frameworks or discover environment variables, launch processes, or run an autonomous agent loop; existing architecture tests enforce these choices. The module system should express capabilities such as “requires caller-provided embedder” or “performs HTTP through injected transport” rather than assuming a library containing connector code is side-effect free.

## Metadata already available

`docs/algorithm-choices.md` compares workloads and tradeoffs across retrieval, context selection, adaptive retrieval, parsing, memory, graphs, agent activity, maintenance, verification, and evaluation. It includes 21 more detailed additions, source citations, formulas, adaptation boundaries, and limitations. Source docstrings frequently specify determinism, filtering requirements, empty-input behavior, approximate versus exact computation, and callback boundaries.

Use this evidence to seed agent-facing family context. Descriptions inferred only from names should be labeled generated. A function signature alone cannot establish substitutability: positive-IDF BM25 differs from the Okapi/L/Plus variants; approximate retrieval should state recall tradeoffs; score fusion should state calibration assumptions; lazy greedy requires submodularity. Algorithm descriptions should record these preconditions in structured fields plus readable context.

Keep source provenance and the Apache-2.0 license and third-party notices with extracted artifacts. The copied project's tests, examples, docs, and fixtures remain valuable provenance even when they are excluded from individual wheel payloads.

## Validation and migration traps

The source tests cover algorithm reference fixtures (BM25 and FastCDC), objective formulas and lazy/naive agreement, graph solver behavior, ranking and filtering, memory revisions and feedback replay, connector contracts, structured parsing, serialization, and composition. Solver tests skip when optionals are absent. Small fixtures establish interface and computation behavior; they do not establish large-scale publication throughput or retrieval quality.

Acceptance checks for the new system should include an artifact built and imported in a fresh interpreter without the copied source tree, an algorithm computation from the installed artifact, absence of unrelated payloads, optional dependency handling, shared class identity when two members compose, content hash verification, reproducible rebuilds, and a complete public-inventory coverage report. Reusing the original semantic tests against generated facades gives broader evidence than a handful of new example-only tests.

`tests/test_architecture.py` explicitly asserts that the old repository publishes one distribution, has no `packages` directory, and is installed as `mark-kit` directly from its source directory. Those packaging assertions describe the old architecture and need to be scoped to the preserved copy or explicitly adapted for the new module system. They are not semantic algorithm compatibility tests. Avoid changing their meaning silently while reporting the entire historical suite as proof of unchanged packaging.

The original `pyproject.toml` includes `py.typed` package data and Apache license notices. Preserve typing metadata and legal notices in generated wheels. A metadata catalog whose members still depend on the entire `mark-kit` wheel would demonstrate discovery but would not satisfy the intended software distribution granularity.
