# Mari extraction validation experiment

Mari Kit is the first migration candidate for the generic package repository system. This experiment checks whether shared definition cells preserve the candidate's existing behavior. It does not establish that all Python projects can be transformed or that the repository has internet-scale operating characteristics.

The copied source contains 988 public function/class declarations. The build generated 988 independently named member wheels and 1,334 shared support-cell wheels, for 2,322 artifacts. Three additional compiled cells were unreachable from the public publication roots and were omitted. The publisher-qualified TOML build preserves these artifact counts and adds the separately published ranking.recency interface. Source discovery supplies the inventory; small contribution fragments add context and signatures. See the current [final validation](final-validation.json) and the earlier [build experiment](mari-build-validation.json). Artifact counts and timings are measurements for this candidate and machine.

Every one of the 988 member exports imported successfully in a fresh interpreter from extracted wheels with both original and copied package source paths removed. No original `mark_kit` module loaded. The standalone compiler tests independently demonstrate a standard-library operation with no unrelated package payload or third-party requirement, shared dataclass identity across separately published operations, mutually recursive definitions with colliding source names, preserved default factories and forward annotations, deterministic wheel bytes and RECORD entries, per-member versions, and unchanged cell identities after unrelated sibling edits.

The broader semantic run passed **429 upstream tests** on Python 3.13.3. It used the copied tests, examples, documentation, reference fixtures and benchmark records. Each wheel's SHA-256, internal RECORD hashes, metadata and paths were verified before extraction. Every source binding used by a historical namespace came from the verified cell installation. The checks cover retrieval, graph solvers, parsing, connectors, memory, trajectories, reference fixtures, evaluation, documentation imports, call signatures, and composition.

Six tests in `tests/test_architecture.py` were excluded because they describe the original monolithic package layout and installation identity. The original candidate's baseline had 435 passing tests. All 429 remaining tests ran, including the authorization regression that verifies the number of candidates passed to approximate scoring.

The test harness makes two explicit compatibility adaptations. First, it supplies synthetic historical `mark_kit` namespaces that delegate bindings to generated cells, so existing test import statements remain usable. Those namespaces never execute the original package source. Second, the one historical monkeypatch of `mark_kit.retrieval.index.polar_scores` forwards to the actual global binding of the compiled `search_index` function. General monkeypatching of historical module globals is not a runtime compatibility promise of definition extraction.

Run the experiment from the repository root with a Python environment containing the candidate's test and optional solver dependencies:

```sh
PYTHONPATH=src python scripts/validate_migration.py --build dist/mari-v2
```

The machine-readable result is [migration-validation.json](migration-validation.json). The machine-readable report records the timing of the latest run. The harness also verifies that no original package module executed. It leaves the copied source unchanged.

These checks support behavioral preservation for the exercised source snapshot. Generated module identities differ from original source paths, affecting introspection and historical pickle/module-path compatibility. Required third-party distributions were already installed in the validation environment; this run does not lock or independently reproduce their entire dependency environment.

The earlier unqualified-publication experiment also exercised standard pip installation separately with `--no-index --no-cache-dir --find-links dist/mari --target <temporary-site> mf-mari-algorithms-temporal-recency-decay==0.1.0`. Pip installed exactly two distributions: the selected member and its single shared cell. A fresh interpreter outside the source checkout imported the installed member and verified `recency_decay(10, method="exponential", half_life=10) == 0.5`. It imported no original `mark_kit` code and installed no third-party dependency. The evidence is recorded in [pip-validation.json](pip-validation.json).
