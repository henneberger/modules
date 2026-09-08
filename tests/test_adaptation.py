from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from module_families.adaptation import AdaptationError, adapt
from module_families.compiler import build_family
from module_families.manifest import write_manifest
from module_families.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mf-projection-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package = self.root / "original" / "example"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text(
            "raise RuntimeError('initializer must not run')\n"
        )
        self.source = self.package / "ops.py"
        self.source.write_text(
            "from itertools import islice, filterfalse\n"
            "print('top-level initialization must not run')\n"
            "try:\n    import omitted_optional_dependency\nexcept ImportError:\n    pass\n"
            "def mark(function):\n    return function\n"
            "@mark\ndef take(items, size):\n    # Keep this source comment.\n    return list(islice(items, size))\n"
        )
        self.manifest = self.root / "projection.toml"
        self.destination = self.root / "projected"

    def declare(self, declarations=None):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest.write_text(
            "schema_version = 1\nname = 'example-projection'\n"
            "[source]\nroot = 'original'\npackage = 'example'\n"
            "[review]\nreason = 'Only these declarations are used by this application.'\n"
            "omit_unselected = true\nempty_package_initializers = true\n"
            "[[modules]]\nname = 'example.ops'\n"
            f"sha256 = '{digest}'\ndeclarations = {json.dumps(declarations or ['mark', 'take'])}\n"
            "imports = ['islice']\n"
        )

    def test_selected_definition_text_decorators_and_omission_evidence_are_preserved(
        self,
    ):
        self.declare()
        original = self.source.read_bytes()
        report = adapt(self.manifest, self.destination)
        projected = (self.destination / "example/ops.py").read_text()
        self.assertIn(
            "@mark\ndef take(items, size):\n    # Keep this source comment.\n    return list(islice(items, size))",
            projected,
        )
        self.assertIn("from itertools import islice", projected)
        self.assertNotIn("filterfalse", projected)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertIn(
            "Try", {row["kind"] for row in report["modules"][0]["excluded_top_level"]}
        )
        self.assertEqual(
            report["modules"][0]["original_sha256"],
            hashlib.sha256(original).hexdigest(),
        )
        self.assertEqual(report, adapt(self.manifest, self.destination))

    def test_missing_dependencies_are_not_invented_and_no_output_is_exposed(self):
        self.declare(["take"])
        with self.assertRaisesRegex(AdaptationError, "Unresolved global.*mark"):
            adapt(self.manifest, self.destination)
        self.assertFalse(self.destination.exists())

    def test_stale_review_and_existing_work_are_not_overwritten(self):
        self.declare()
        self.source.write_text(self.source.read_text() + "\n# Upstream changed.\n")
        with self.assertRaisesRegex(AdaptationError, "source hash differs"):
            adapt(self.manifest, self.destination)
        self.assertFalse(self.destination.exists())
        self.declare()
        adapt(self.manifest, self.destination)
        changed = self.destination / "example/ops.py"
        changed.write_text("# User changes\n")
        with self.assertRaisesRegex(AdaptationError, "different work"):
            adapt(self.manifest, self.destination)
        self.assertEqual(changed.read_text(), "# User changes\n")

    def test_review_and_destination_separation_are_required(self):
        self.declare()
        with self.assertRaisesRegex(AdaptationError, "separate"):
            adapt(self.manifest, self.package / "generated")
        self.manifest.write_text(
            self.manifest.read_text().replace(
                "omit_unselected = true", "omit_unselected = false"
            )
        )
        with self.assertRaisesRegex(AdaptationError, "explicit review"):
            adapt(self.manifest, self.destination)


_BEHAVIOR_WORKER = r"""
import json, sys
from pathlib import Path
mode, work = sys.argv[1], Path(sys.argv[2])
if mode == 'upstream':
    from more_itertools import chunked, unique_everseen
    from boltons.iterutils import chunked_iter, unique_iter
    providers = {'more-itertools': (chunked, unique_everseen), 'boltons': (chunked_iter, unique_iter)}
else:
    from module_families.registry import Registry
    from module_families.runtime import load
    from module_families.interfaces import signature_from_spec
    repository = Registry(work / 'registry')
    providers = {}
    for vendor in ('more-itertools', 'boltons'):
        chunk = load(repository, repository.lock('iterators', vendor + '.chunked'), work / 'installed' / vendor / 'chunk')
        unique = load(repository, repository.lock('iterators', vendor + '.unique'), work / 'installed' / vendor / 'unique')
        signature_from_spec(repository.interface('iterators.chunker', '1')).seal({'chunk': chunk})
        signature_from_spec(repository.interface('iterators.unique', '1')).seal({'unique': unique})
        providers[vendor] = (chunk, unique)
    assert 'more_itertools' not in sys.modules and 'boltons' not in sys.modules

def normalize(value):
    if isinstance(value, bytes):
        return {'bytes': list(value)}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value

def outcome(function, *args):
    try:
        return {'value': normalize(list(function(*args)))}
    except Exception as error:
        return {'error': type(error).__name__}

results = {}
for vendor, (chunk, unique) in providers.items():
    chunks = [outcome(chunk, data, size)
              for data in ([], [1], [1,2,3,4,5], tuple(range(5)), range(5))
              for size in (1,2,3,7)]
    chunks.append(outcome(chunk, (item for item in range(5)), 2))
    invalid = {str(size): outcome(chunk, [1,2,3], size) for size in (0,-1,1.5,'2',None,True,False)}
    invalid['not-iterable'] = outcome(chunk, 42, 2)
    unique_results = [outcome(unique, data) for data in ([], [1,2,1,3,2], ['a','b','a'], [1,True,2,False,0,2], [[1],[1]], 42, 'abba')]
    unique_results.append(outcome(unique, (item % 3 for item in range(10))))
    results[vendor] = {'chunks': chunks, 'invalid_sizes': invalid, 'unique': unique_results,
                       'strings': outcome(chunk, 'abcde', 2), 'bytes': outcome(chunk, b'abcde', 2)}
print(json.dumps(results, sort_keys=True))
"""


class ExistingLibraryParityTests(unittest.TestCase):
    def test_installed_independent_providers_match_their_unchanged_upstreams(self):
        with TemporaryDirectory(prefix="mf-upstream-parity-") as temporary:
            work = Path(temporary).resolve()
            source_hashes = {}
            for vendor in ("more-itertools", "boltons"):
                upstream = ROOT / "projects" / vendor
                source_hashes[vendor] = json.loads(
                    (upstream / "UPSTREAM.json").read_text()
                )["files"]
                adapt(
                    ROOT / "adaptations" / f"{vendor}.toml",
                    work / "projections" / vendor,
                )
            repository = Registry(work / "registry")
            build_family(
                ROOT / "families/iterators/interfaces.toml", work / "interfaces"
            )
            repository.publish(work / "interfaces/index.json")
            artifacts = 0
            for vendor in ("more-itertools", "boltons"):
                original_manifest = ROOT / "families/iterators" / f"{vendor}.toml"
                declaration = tomllib.loads(original_manifest.read_text())
                declaration["source"]["root"] = str(work / "projections" / vendor)
                declaration["source"]["license_files"] = [
                    str((original_manifest.parent / file).resolve())
                    for file in declaration["source"]["license_files"]
                ]
                manifest = write_manifest(declaration, work / f"{vendor}.toml")
                index = build_family(manifest, work / vendor)
                self.assertEqual(len(index["members"]), 2)
                self.assertEqual(index["publisher"], vendor)
                artifacts += len(index["artifacts"])
                repository.publish(work / vendor / "index.json")
            self.assertEqual(artifacts, 11)
            reports = {}
            for mode in ("upstream", "installed"):
                paths = [str(ROOT / "src")]
                if mode == "upstream":
                    paths.extend(
                        str(ROOT / "projects" / name)
                        for name in ("more-itertools", "boltons")
                    )
                process = subprocess.run(
                    [sys.executable, "-c", _BEHAVIOR_WORKER, mode, str(work)],
                    cwd=work,
                    env={
                        **os.environ,
                        "PYTHONPATH": os.pathsep.join(paths),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    capture_output=True,
                    text=True,
                    check=True,
                )
                reports[mode] = json.loads(process.stdout)
            self.assertEqual(reports["upstream"], reports["installed"])
            more, boltons = (
                reports["installed"][name] for name in ("more-itertools", "boltons")
            )
            self.assertEqual(more["chunks"], boltons["chunks"])
            self.assertEqual(more["unique"], boltons["unique"])
            self.assertEqual(more["strings"]["value"], [["a", "b"], ["c", "d"], ["e"]])
            self.assertEqual(boltons["strings"]["value"], ["ab", "cd", "e"])
            self.assertNotEqual(more["invalid_sizes"], boltons["invalid_sizes"])
            for vendor, files in source_hashes.items():
                for relative, digest in files.items():
                    content = (ROOT / "projects" / vendor / relative).read_bytes()
                    self.assertEqual(hashlib.sha256(content).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
