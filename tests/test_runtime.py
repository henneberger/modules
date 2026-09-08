"""Loading must not silently substitute foreign cached module source."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from module_families.registry import Registry, RegistryError
from module_families.runtime import _import_member, load
from module_families.wheels import build_wheel


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.saved_modules = {
            name: module
            for name, module in sys.modules.copy().items()
            if name in ("mf_cells", "mf_members")
            or name.startswith(("mf_cells.", "mf_members."))
        }
        self.saved_path = sys.path[:]
        for name in self.saved_modules:
            sys.modules.pop(name, None)
        self.addCleanup(self.restore_modules)
        self.member_name = "mf_members.m_" + "a" * 64
        self.cell_name = "mf_cells.c_" + "b" * 64
        build = self.root / "build"
        cell = build_wheel(
            build,
            distribution="runtime-cell",
            version="1.0",
            files={
                self.cell_name.replace(".", "/")
                + ".py": "class Shared: pass\nvalue = Shared()\n"
            },
        )
        cell["dependencies"] = []
        facade = build_wheel(
            build,
            distribution="runtime-member",
            version="1.0",
            files={
                self.member_name.replace(".", "/")
                + ".py": f"from {self.cell_name} import value\n"
            },
            requires_dist=["runtime-cell==1.0"],
        )
        facade["dependencies"] = ["runtime-cell"]
        card = {
            "id": "fixture.example",
            "publisher": "fixture",
            "local_id": "example",
            "effects": [],
            "distribution": facade["distribution"],
            "wheel": facade["filename"],
            "sha256": facade["sha256"],
            "import_module": self.member_name,
            "export": "value",
        }
        index = {
            "schema_version": 1,
            "publisher": "fixture",
            "family": {"name": "test", "version": "1.0"},
            "members": [card],
            "artifacts": [cell, facade],
        }
        path = build / "index.json"
        path.write_text(json.dumps(index))
        self.registry = Registry(self.root / "registry")
        self.registry.publish(path)
        self.lock = self.registry.lock("test", "fixture.example")

    def restore_modules(self):
        for name in list(sys.modules):
            if name in ("mf_cells", "mf_members") or name.startswith(
                ("mf_cells.", "mf_members.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(self.saved_modules)
        sys.path[:] = self.saved_path

    def foreign_cached(self, module_name):
        source = self.root / (module_name.replace(".", "_") + ".py")
        source.write_text("value = 'wrong code'\n")
        module = ModuleType(module_name)
        module.__file__ = str(source)
        module.value = "wrong code"
        sys.modules[module_name] = module

    def test_cached_member_from_different_source_is_rejected(self):
        self.foreign_cached(self.member_name)
        with self.assertRaisesRegex(ValueError, "cached source differs"):
            load(self.registry, self.lock, self.root / "installed")

    def test_cached_dependency_from_different_source_is_rejected(self):
        self.foreign_cached(self.cell_name)
        with self.assertRaisesRegex(ValueError, "cached source differs"):
            load(self.registry, self.lock, self.root / "installed")

    def test_identical_source_copies_share_existing_python_identity(self):
        first = load(self.registry, self.lock, self.root / "first")
        second = load(self.registry, self.lock, self.root / "second")
        self.assertIs(first, second)

    def test_cached_generated_module_without_source_is_rejected(self):
        sys.modules[self.member_name] = ModuleType(self.member_name)
        with self.assertRaisesRegex(ValueError, "no verifiable cached source"):
            load(self.registry, self.lock, self.root / "installed")

    def test_ordinary_cached_namespace_is_rejected(self):
        self.foreign_cached("mf_members")
        with self.assertRaisesRegex(ValueError, "ordinary Python package"):
            load(self.registry, self.lock, self.root / "installed")

    def test_ambient_package_shadow_is_rejected_without_running_initializer(self):
        shadow = self.root / "shadow"
        package = shadow / "mf_members"
        package.mkdir(parents=True)
        marker = self.root / "initializer-ran"
        (package / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        )
        sys.path.insert(0, str(shadow))
        with self.assertRaisesRegex(ValueError, "shadowed"):
            load(self.registry, self.lock, self.root / "installed")
        self.assertFalse(marker.exists())

    def test_preexisting_target_initializer_is_rejected(self):
        target = self.root / "installed"
        self.registry.materialize(self.lock, target)
        (target / "mf_members/__init__.py").write_text(
            "raise AssertionError('must not execute')\n"
        )
        with self.assertRaisesRegex(ValueError, "ordinary package initializer"):
            _import_member(self.lock["member"], target)

    def test_selected_destination_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = self.root / "linked"
        target.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RegistryError, "Symlink"):
            load(self.registry, self.lock, target)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
