# Compiling a Python project into publication units

The compiler adapts an existing Python package using a compact TOML manifest. It parses source without importing the candidate package. A family describes the public inventory; each selected function or class can be built and published with its own version and exact shared implementation closure. A contributor does not need to maintain one central declaration for every algorithm.

For a package at `my-project/src/my_library`, a standalone `family.toml` can contain:

```toml
schema_version = 1

[publisher]
name = "alice"

[family]
name = "my-family"
version = "0.1.0"
description = "Algorithms and reusable types for my project."

[source]
root = "my-project/src"
package = "my_library"
license_files = ["my-project/LICENSE"]

[discovery]
public = true

[contributions]
include = ["members/**/*.toml"]

[external_dependencies]
numpy = "numpy>=1.26"
```

Discovery produces minimal cards in memory from public top-level functions and classes, their docstring summaries, and source-relative names. For publisher `alice`, `my_library/solvers.py` defining `solve` becomes member `alice.solvers.solve`. Re-exports do not duplicate the inventory. Private helpers remain available as implementation dependencies. No generated full catalog needs to be checked into the source repository. Family manifests require TOML and a publisher; `[tool.module-families]` tables in `pyproject.toml` are also accepted. JSON is generated interchange, not a supported authored family manifest.

A contribution file such as `members/solvers.toml` only supplies authored differences:

```toml
[[members]]
id = "solvers.solve"
version = "0.2.0"
summary = "Solve a sparse linear system."
effects = ["cpu"]
tags = ["linear-algebra", "sparse"]
```

These fields are author claims. Discovery defaults effects to `unknown`; it does not infer an effect proof. See [the build system guide](build-system.md) for contribution and discovery rules.

Use `plan-build` before emission, selecting one or several members:

```sh
mf plan-build family.toml --member alice.solvers.solve
mf build family.toml --member alice.solvers.solve --out dist/solvers-solve
```

The corresponding Python APIs are:

```python
from pathlib import Path
from module_families.compiler import compile_project, plan_build, build_family

compiled = compile_project(
    Path("my-project/src"),
    package="my_library",
    family="my-family",
    external_dependencies={"numpy": "numpy>=1.26"},
    dynamic_dependencies={},
)
plan = plan_build(Path("family.toml"), member_ids=["alice.solvers.solve"])
index = build_family(
    Path("family.toml"), Path("dist/solvers-solve"), member_ids=["alice.solvers.solve"]
)
```

`plan_build()` returns plain JSON-compatible data with `status: ready` or `status: blocked`. A ready plan shows each selected member's root cells, dependency-first transitive cells, and unresolved external requirements. It includes exact source-binding dependency edges, initialization dependency edges, and referenced top-level external import bindings. Aggregate counts compare the complete public inventory and compiled source cells with the reachable, omitted, and shared cells for the selection. Shared means used by more than one selected member. Source definitions count declaration units, including assignments and private helpers; public inventory counts publication cards. The plan lists only selected source relationships and cells, rather than reproducing the entire inventory. Generated source byte counts cover implementation cells; they are not compressed wheel sizes or facade sizes.

Planning and building share the same preparation, graph analysis, strongly connected components, source validation, license reads, and exact closure routine. Planning generates and syntax-checks cells in memory or reuses verified local analysis state, writes no wheel artifacts, and executes no candidate code. An explicit planning cache may write analysis JSON. The CLI exits with status 2 for a blocked plan while still producing its JSON. Diagnostics currently stop at the first blocking error.

A public function or class is a publication root. A root's referenced definitions, assignments and import bindings determine its implementation closure. Ordinary internal `from package.module import name` imports, relative imports and package re-exports are resolved to the owning binding. Parent package initializers therefore do not pull unrelated operations into the artifact. Functions and classes that depend on each other are combined into a shared strongly connected component, with initialization dependencies ordered before their consumers.

Generated cells share the `mf_cells` namespace, and independently versioned member facades share `mf_members`. Namespace packages contain no overlapping `__init__.py` files. An ordinary facade exports both the original symbol name and `value`. A `kind = "module"` member instead groups an `exports` mapping of public names to source symbols; its `value` is the resulting export mapping, and its closure includes all export roots. This supports reusable libraries of several nominal types. Root `[[interfaces]]` declarations publish their callable/type specifications, including interface-only builds with no source package or wheels. A common supporting class is defined by a single installed cell, preserving its Python identity across members. Cell identities include qualified source bindings, selected AST statements, dependency cell identities, external requirement declarations, family identity and compiler format. An unrelated sibling edit does not change the selected operation's cell identity.

The current source subset supports module docstrings, explicit import statements, function and class declarations, simple named assignments, named annotated assignments with values, postponed and quoted annotations, decorators, default arguments, function-local external imports and the common import-only `if TYPE_CHECKING` form. Literal `__all__` declarations and their extensions are facade metadata. Function bodies can use ordinary control flow, nested functions, local scopes and comprehensions. Class bodies preserve their ordered local namespace lookup. Package initializers may contain real implementations.

The compiler rejects unsupported top-level execution, wildcard imports, rebinding a module global, top-level destructuring or attribute assignment, unbound annotated globals, PEP 695 type parameters, function-local internal imports, internal module-object imports such as `import my_library.module`, unresolved global references, reflective execution such as `exec` or `globals()`, and explicit mutable `global` declarations. Prefer explicit internal `from` imports for now. Unsupported constructs fail with the relevant source binding instead of silently producing a partial publication. This is a constrained source compiler, not a proof of equivalence for arbitrary Python or a security sandbox.

In manifests, declare third-party dependencies by import root using a requirement string, and per-definition dynamic dependencies using a list of strings. Import roots and distribution names differ in some packages:

```toml
[external_dependencies]
numpy = "numpy>=1.26"
torch_geometric = "torch-geometric>=2"

[dynamic_dependencies]
"my_library.solvers:select_backend" = ["networkx>=3.2"]
"my_library.solvers:_load_audited_backend" = []
```

Function-local imports stay local, and their requirements attach to the owning definition. Literal `importlib.import_module("package.submodule")` calls are traced to the declared import root. Variable dynamic imports require an explicit declaration keyed by the original `module:symbol`. An empty list means an audited helper delegates dependency choice to its callers; callers must declare their actual dependencies. These declarations are author assertions, and dynamic loading still requires review and workload testing.

Member versions may override the family's default `version`, enabling separate algorithm releases. Internal cell requirements are pinned exactly. Ordinary third-party PEP 508 requirements remain unresolved at build/member-lock time. The separate `lock-env` workflow resolves a chosen assembly's wheel environment and fingerprints its interpreter; `sync` and `exec` replay it offline.

Wheels use fixed timestamps, deterministic entry order, explicit requirements, SHA-256 RECORD entries and retained license files. Before writing a wheel, the builder compares its complete regenerated bytes with an existing file and preserves a byte-identical file, including its modification time. An old filename or timestamp alone is never a cache hit; different or corrupted bytes are replaced in the local build directory. Immutable publication rules still apply when publishing a changed member to the registry, so a changed release requires a new member version.

Cell identities depend on the selected source statements and their dependencies, so an unrelated algorithm edit preserves the selected cells. Automatically discovered cards omit whole-file digests and source line numbers, preserving facade wheel bytes when an unrelated same-file edit shifts line numbers. A shared dependency edit changes its cell and every dependent cell. Facades also include the member card, publisher, family context, and license metadata: changing those can change a wheel even when executable source is unchanged. The index records a whole-source digest for the build observation; that digest can change while all selected wheel bytes remain stable.

Source symbols and publication context are recorded as artifact provenance. The Python API exposes compiled cell/source identities for inspection. Actual execution occurs when a consumer explicitly imports an installed member.

Use `[sources.alias]` tables instead of `[source]` to compile several disjoint package trees together. Supported explicit internal imports resolve across those roots, preserving shared cells. Discovery adds the source alias before publisher qualification, for example `alice.core.solvers.solve`. The lower-level `compile_sources()` API accepts the same root/package mappings with filesystem paths.

`discovery.include` and `discovery.exclude` select public inventory, while optional `modules = ["my_library.solvers", "my_library.records"]` on a source table limits the compiler's module scope. Internal dependencies outside that scope produce an error and must be added explicitly. Without a module scope, each registered package is analyzed in full, so unsupported initialization in an unrelated file can still block the build.

Builds use a persistent JSON analysis cache at `out/.cache` by default; `cache_dir=` or CLI `--cache` selects another directory. Planning uses caching only when explicitly requested. Every lookup hashes source bytes, compiler bytes, interpreter identity, source scopes, and dependency declarations. A matching entry avoids graph reconstruction and cell generation; an input change invalidates the complete cached analysis. Corrupt entries are recomputed. Cache files are local trusted build state, not an artifact exchange format. Discovery still reads source, and wheels are still regenerated in memory for byte comparison. `build-stats.json` records cache observations separately from the immutable index. Fine-grained incremental parsing and distributed build execution remain future work.

Each invocation writes one `index.json` containing its selected subset and does not merge older local build indexes. Use distinct output directories when retaining multiple publication inputs. The registry can accept disjoint subsets incrementally under the same family header, and members can release independently. Omitted old wheels may remain in a reused directory, but only artifacts referenced by its current index participate in that publication.

Generated Python module paths differ from source module paths. Exact `__module__` introspection, original-package monkeypatch paths and historical pickle identities need migration treatment. The temporary namespace bridge used in the Mari validation experiment is test infrastructure, not the package repository's public execution interface.
