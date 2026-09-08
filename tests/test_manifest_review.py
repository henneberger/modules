"""Regression cases for stable discovery identities across contributions."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from module_families.manifest import ManifestFormatError, load_manifest, write_manifest


class SourceAliasReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mf-manifest-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package = self.root / "src" / "sample"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text("")
        (self.package / "zeta.py").write_text("def run(value):\n    return value + 1\n")
        self.document = {
            "schema_version": 1,
            "publisher": {"name": "sample"},
            "family": {
                "name": "sample",
                "version": "0.1.0",
                "description": "Sample family",
            },
            "source": {"root": "src", "package": "sample"},
            "discovery": {"public": True},
        }
        self.manifest = self.root / "family.toml"

    def load(self):
        write_manifest(self.document, self.manifest)
        return load_manifest(self.manifest)

    def test_adding_source_alias_does_not_silently_rename_existing_member(self):
        self.assertEqual([m["id"] for m in self.load()["members"]], ["sample.zeta.run"])
        (self.package / "alpha.py").symlink_to(self.package / "zeta.py")
        with self.assertRaisesRegex(
            ManifestFormatError, "source paths cannot use symlinks"
        ):
            self.load()

    def test_discovery_through_a_symlinked_directory_is_rejected(self):
        real = self.package / "real"
        real.mkdir()
        (real / "operation.py").write_text("def compute():\n    return 2\n")
        (self.package / "alias").symlink_to(real, target_is_directory=True)
        self.document["discovery"]["include"] = ["alias/*.py"]
        with self.assertRaisesRegex(
            ManifestFormatError, "source paths cannot use symlinks"
        ):
            self.load()

    def test_symlinked_package_root_is_rejected(self):
        (self.root / "src" / "alias").symlink_to(self.package, target_is_directory=True)
        self.document["source"]["package"] = "alias"
        with self.assertRaisesRegex(ManifestFormatError, "source root uses a symlink"):
            self.load()

    def test_internal_contribution_alias_still_merges_exactly_once(self):
        contributions = self.root / "members"
        contributions.mkdir()
        fragment = contributions / "zeta.toml"
        fragment.write_text('[[members]]\nid = "zeta.run"\neffects = []\n')
        (contributions / "alias.toml").symlink_to(fragment)
        self.document["contributions"] = {"include": ["members/*.toml"]}
        self.assertEqual(self.load()["members"][0]["effects"], [])


if __name__ == "__main__":
    unittest.main()
