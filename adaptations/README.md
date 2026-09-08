# Reviewed source projections

These TOML recipes adapt preserved upstream Python without rewriting the selected algorithms:

```sh
PYTHONPATH=src python scripts/prepare_candidates.py
```

The command prepares more-itertools 11.1.0 and Boltons 25.0.0 under `adaptations/generated/`. That directory is generated and ignored. The original projects remain under `projects/`, with their downloaded artifact identities and file hashes in `UPSTREAM.json`.

Each recipe pins the complete original module hash and explicitly lists declarations and imported bindings. Selected function text, including decorators, comments, and bodies, is preserved. Import statements can be narrowed to declared aliases. Package initializers and unselected top-level statements are omitted only because the recipe explicitly authorizes that projection. Missing helpers fail compilation; the adapter does not synthesize replacements.

Each output includes `ADAPTATION.json`: original and projected hashes, selected symbols, omitted statement ranges, import changes, and the review reason. The ordinary compiler checks the projected dependency graph without importing candidate code before files are exposed. Rerunning an identical projection is allowed; different existing output is never overwritten.

The report is evidence of a reproducible projection and successful static checks. It does not prove arbitrary behavioral equivalence or preserve the entire original package's initialization semantics. Updating an upstream source file invalidates its hash pin and requires a new explicit review.

The installed parity test compares each extracted iterator with its original upstream implementation, including empty input, generators, invalid sizes, unhashable items, strings, and bytes:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_adaptation.py -v
```

The shared chunking law covers non-string iterables and strictly positive integer sizes. more-itertools produces lists from a string; Boltons produces string chunks. Their invalid-size behaviors also differ. The tests preserve and document those differences rather than flattening them into a false universal contract.
