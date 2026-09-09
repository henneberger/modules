# Validation

The cleanup passes **680 tests and 32 subtests**, Ruff, and an isolated wheel build. The wheel excludes the removed orchestration and migration modules. See [current validation](final-validation.json).

Current checks cover the module compiler, typed composition, build outputs, and ordinary Python execution. Agent execution and coordination tooling has been removed. Historical measurements below describe their recorded versions.

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

The current implementation includes an authenticated HTTP repository and a separate wheel-only environment resolver. Local TCP integration validates the protocol, not internet-scale capacity. Internet-scale concurrent operation, fine-grained incremental parsing remain evaluation targets in the [research proposal](RESEARCH.md). The analysis cache invalidates the full scoped graph when its inputs change; environment resolution delegates package selection to pip and excludes source builds.
