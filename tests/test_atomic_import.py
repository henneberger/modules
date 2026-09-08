"""Atomic bundle visibility and preflight behavior with independent wheel fixtures."""

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from module_families.contracts import ContractError, Functor, Requirement, Signature
from module_families.registry import Registry, RegistryError
from module_families.runtime import atomic_import
from module_families.wheels import build_wheel


def next_contract():
    raise AssertionError("signature prototype invoked")


class AtomicImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="atomic-import-test-")
        self.root = Path(self.temporary.name).resolve()
        self.registry = Registry(self.root / "repository")
        self.build = self.root / "wheels"
        self.target = self.root / "environments"
        self.old_modules = set(sys.modules)
        self.old_path = list(sys.path)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Fixture module names contain temporary paths in their source digest.
        # Remove only newly imported generated modules before deleting fixtures.
        for name in set(sys.modules) - self.old_modules:
            if name in {"mf_members", "mf_cells"} or name.startswith(
                ("mf_members.", "mf_cells.")
            ):
                sys.modules.pop(name, None)
        sys.path[:] = self.old_path
        self.temporary.cleanup()

    def fixtures(self, *, conflict=False):
        artifacts, members, markers = [], [], {}
        first_module = None
        for name in ("a", "b"):
            marker = self.root / f"imported-{name}"
            markers[name] = marker
            source = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('imported')\n"
                "class Token: pass\n"
                "def make():\n"
                "    state = []\n"
                "    def next_value():\n"
                "        state.append(None)\n"
                "        return len(state)\n"
                "    return {'next': next_value, 'Token': Token}\n"
            )
            digest = hashlib.sha256(source.encode()).hexdigest()
            module = f"mf_members.m_{digest}"
            if first_module is None:
                first_module = module
            if conflict:
                module = first_module
            artifact = build_wheel(
                self.build,
                distribution=f"atomic-fixture-{name}",
                version="1.0",
                files={module.replace(".", "/") + ".py": source},
            )
            artifact["dependencies"] = []
            artifacts.append(artifact)
            members.append(
                {
                    "id": "fixture." + name,
                    "publisher": "fixture",
                    "local_id": name,
                    "version": "1.0",
                    "summary": "Independent local module factory",
                    "kind": "module-factory",
                    "effects": ["local-file-write"],
                    "distribution": artifact["distribution"],
                    "wheel": artifact["filename"],
                    "sha256": artifact["sha256"],
                    "import_module": module,
                    "export": "make",
                }
            )
        index = {
            "schema_version": 1,
            "publisher": "fixture",
            "family": {
                "name": "atomic-fixture",
                "version": "1.0",
                "description": "Atomic import fixtures",
                "context": {},
            },
            "artifacts": artifacts,
            "members": members,
        }
        path = self.build / "index.json"
        path.write_text(json.dumps(index))
        self.registry.publish(path)
        locks = {
            name: self.registry.lock("atomic-fixture", "fixture." + name, "1.0")
            for name in ("a", "b")
        }
        return locks, markers

    def assert_not_imported(self, locks, markers):
        self.assertFalse(any(marker.exists() for marker in markers.values()))
        self.assertFalse(self.target.exists())
        for lock in locks.values():
            self.assertNotIn(lock["member"]["import_module"], sys.modules)

    def test_invalid_second_lock_prevents_every_selected_import(self):
        locks, markers = self.fixtures()
        damaged = deepcopy(locks)
        damaged["b"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RegistryError, "Lock hash mismatch"):
            atomic_import(self.registry, damaged, self.target)
        self.assert_not_imported(locks, markers)

    def test_damaged_second_artifact_prevents_every_selected_import(self):
        locks, markers = self.fixtures()
        artifact = locks["b"]["artifacts"][0]
        self.registry._blob(artifact["sha256"]).write_bytes(b"damaged fixture")
        with self.assertRaises(RegistryError):
            atomic_import(self.registry, locks, self.target)
        self.assert_not_imported(locks, markers)

    def test_conflicting_artifact_files_fail_before_import(self):
        locks, markers = self.fixtures(conflict=True)
        with self.assertRaisesRegex(RegistryError, "multiple artifacts own"):
            atomic_import(self.registry, locks, self.target)
        self.assert_not_imported(locks, markers)

    def test_failed_link_returns_no_bundle_but_does_not_rollback_python_effects(self):
        locks, markers = self.fixtures()
        linked = []
        sentinel = object()
        bundle = sentinel

        def fail(exports):
            linked.append(tuple(sorted(exports)))
            raise ContractError("fixture linking failed")

        with self.assertRaisesRegex(ContractError, "fixture linking failed"):
            bundle = atomic_import(self.registry, locks, self.target, link=fail)
        self.assertIs(bundle, sentinel)
        self.assertEqual(linked, [("a", "b")])
        self.assertTrue(all(marker.exists() for marker in markers.values()))
        self.assertTrue(
            all(
                lock["member"]["import_module"] in sys.modules
                for lock in locks.values()
            )
        )

    def test_same_graph_replays_with_fresh_factory_state(self):
        locks, _ = self.fixtures()
        first = atomic_import(
            self.registry, locks, self.target, link=lambda exports: exports["a"]()
        )
        second = atomic_import(
            self.registry,
            dict(reversed(list(locks.items()))),
            self.target,
            link=lambda exports: exports["a"](),
        )
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(first.target, second.target)
        self.assertIs(first.exports["a"], second.exports["a"])
        self.assertEqual(first.value["next"](), 1)
        self.assertEqual(first.value["next"](), 2)
        self.assertEqual(second.value["next"](), 1)
        self.assertIsNot(first.value, second.value)
        self.assertEqual(first.external_requirements, ())
        with self.assertRaises(TypeError):
            first.exports["partial"] = None
        with self.assertRaises(TypeError):
            first["partial"] = None

    def test_nominal_conflict_prevents_linked_bundle_and_factory_invocation(self):
        locks, _ = self.fixtures()
        signature = Signature(
            "atomic.counter", "1", {"next": next_contract}, ("Token",)
        )
        factory_calls = []

        def factory(*, a, b):
            factory_calls.append((a, b))
            return {}

        combine = Functor(
            "combine@1",
            {"a": Requirement(signature), "b": Requirement(signature)},
            Signature("empty", "1"),
            factory,
            sharing=(("a.Token", "b.Token"),),
        )

        def link(exports):
            return combine(
                a=signature.seal(exports["a"]()), b=signature.seal(exports["b"]())
            )

        sentinel = object()
        bundle = sentinel
        with self.assertRaisesRegex(ContractError, "type sharing conflict"):
            bundle = atomic_import(self.registry, locks, self.target, link=link)
        self.assertIs(bundle, sentinel)
        self.assertEqual(factory_calls, [])

    def test_bundle_identity_does_not_claim_to_identify_arbitrary_link_callback(self):
        locks, _ = self.fixtures()
        first = atomic_import(
            self.registry, locks, self.target, link=lambda exports: "behavior A"
        )
        second = atomic_import(
            self.registry, locks, self.target, link=lambda exports: "behavior B"
        )
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.value, second.value)


if __name__ == "__main__":
    unittest.main()
