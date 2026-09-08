# Validation of the research build system

Current integrated test results and end-to-end checks are recorded in [final-validation.json](final-validation.json). The source artifacts below record the interpreter and scope of each measurement. Tests cover the implemented fragment; they do not establish correctness for arbitrary Python or internet-scale operation.

Version 0.4.0 passes **392 tests and 32 subtests**. The new checks exercise typed interface validation, separate compilation, ownership transfers and borrows, typed graph projections, branch accounting, effect bounds, and deterministic generated wheels. The [typed-language report](typed-language-validation.json) records interface-only compilation followed by SQLite provider publication, synthesis, installation, and offline execution returning `["module systems", "committed"]`. Invalid use-after-move and resource-leak examples are rejected before execution. Ruff and the distributable 0.4.0 wheel build pass.

The [historical 0.3.0 report](final-validation-0.3.json) records **281 tests and 32 subtests**, including 22 new graph-linking checks. Those cover partial/nested publication, explicit mixin order, shared versus fresh instances, export renaming, nominal checks, semantic-index propagation/rejection, preflight, tampered artifacts, CLI authoring, and a real offline environment replay. Ruff and the distributable wheel build pass.

The [0.3.0 candidate report](module-build-validation.json) records a complete graph built from unchanged Mari MMR source, scikit-learn lexical vectors, a cache mixin, SQLite, and a Python executor. The compiled system returns the expected first hits `dogs` and `python` in its locked offline environment. Source preservation was checked; the original `~/mari-kit` working tree remains clean. The [0.2.0 report](final-validation-0.2.json) retains earlier Mari semantic and 10,000-definition measurements; those larger historical experiments were not rerun for this linker extension.

## Measured workloads

| Experiment | Observed result | Practical meaning |
|---|---|---|
| Compact authoring | Projects with 1 and 1,000 public definitions produce identical root TOML | Source inventory does not expand the authored declaration |
| Distributed contributions | 10,000 definitions, 1,000 contribution files, a 21-line / 279-byte root TOML | Contributions can be merged without a central member list |
| Selected build in that workload | One member, two source cells, three wheels | Unselected algorithms are omitted from the emitted dependency closure |
| Repeated build | Identical wheel bytes and modification times, with cached source analysis | Unchanged inputs reuse the coarse analysis cache and preserve artifacts |
| Source change propagation | Unrelated same-file edit preserves selected artifacts; shared dependency edit changes them | Source cell identities follow the tested declaration dependencies |
| Copied Mari build | 988 public definitions, 988 member wheels, 1,334 shared cell wheels | Existing software supplies a separate migration workload |
| Mari authoring | 43-line root TOML, seven annotations in two contributor files | Expanded source facts are generated rather than maintained by hand |
| Mari semantic regression | 429 upstream tests pass against verified generated cells | Exercised behavior survives extraction with documented test-namespace adaptations |
| Ordinary offline pip | Two installed distributions; recency result is 0.5 | A selected algorithm installs without the whole source package or unrelated libraries |

The recorded 0.2.0 synthetic workload took approximately 0.36 seconds for discovery, 2.39 seconds for planning, and 2.59 seconds for a selected build on the recorded machine. A repeated build using the persistent analysis cache took approximately 0.58 seconds. [Full workload and measurements](build-scale-validation.json) distinguish synthetic inputs from production packages.

The current suite checks publisher-required TOML and `pyproject.toml` authoring, contributor conflicts, source scopes and multiple roots, persistent analysis-cache invalidation, import-free discovery, grouped type exports, interface-only publications, immutable publisher ownership, exact provider/version selection, recursive capability synthesis, program locks, and checked module composition. Real TCP tests exercise independent publishers joining one family, authorization failures, verified selected downloads, archive limits, and fresh-interpreter remote atomic imports. Separate environment tests cover wheel resolution, hash verification, interpreter matching, offline synchronization, and execution. Unsupported source produces a blocked plan with its first diagnostic. See the final report for the exact tests run and outcomes; no type-theoretic proof follows from them.

The copied candidate's original-source baseline passed 435 tests. The migrated run excludes six tests about the old package layout and installation identity. Its test-only historical namespaces delegate to verified generated cells, and one old monkeypatch is redirected to the actual compiled global binding. No original `mark_kit` implementation executes. See [migration method and results](migration-validation.md), [current integration report](final-validation.json), and [offline pip evidence](pip-validation.json). The original `~/mari-kit` working tree remains unchanged.

The independent payment fixture checks module semantics rather than defining the build model. The same published wrappers, in different orders, produce one versus twenty ledger entries for one simulated charge effect. This demonstrates an observable composition difference under the fixture's assumptions; it does not establish general retry, idempotency, or exactly-once laws.

Reproduce the main checks from the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m pytest tests -q
ruff check src tests examples scripts
python3 scripts/benchmark_build.py --members 10000 --fragments 1000
python3 -m module_families build families/mari/family.toml --out dist/mari-v2
# Requires the candidate's test and optional numerical dependencies:
python3 scripts/validate_migration.py --build dist/mari-v2
python3 examples/typed_system.py --work-dir .mf/typed-example
python3 -m build --wheel
```

The current implementation includes an authenticated HTTP repository and a separate wheel-only environment resolver. Local TCP integration validates the protocol, not internet-scale capacity. Parallel contributor throughput, fine-grained incremental parsing, federation, and controlled agent adaptation quality remain evaluation targets in the [research proposal](RESEARCH.md). The analysis cache invalidates the full scoped graph when its inputs change; environment resolution delegates package selection to pip and excludes source builds.
