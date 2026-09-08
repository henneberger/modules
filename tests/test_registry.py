"""Publication and replay checks exercise bytes, metadata, and graph boundaries."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from module_families.registry import Registry, RegistryError, canonical_bytes


def wheel(
    directory,
    distribution,
    *,
    dependencies=(),
    external=(),
    source=None,
    extra=None,
    symlinks=(),
    bad_record=False,
    version="1.0",
):
    stem = distribution.replace("-", "_")
    filename = f"{stem}-{version}-py3-none-any.whl"
    dist_info = f"{stem}-{version}.dist-info"
    requires = [f"{name}=={version}" for name in dependencies] + list(external)
    files = {
        f"{stem}/__init__.py": (source or "value = 42\n").encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {req}\n" for req in requires)
            + "\n"
        ).encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: registry-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files.update(extra or {})
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in sorted(files.items()):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(content).digest())
            .rstrip(b"=")
            .decode()
        )
        if bad_record and name.endswith("__init__.py"):
            digest = "incorrect"
        writer.writerow([name, f"sha256={digest}", len(content)])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()
    path = directory / filename
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (
                (stat.S_IFLNK if name in symlinks else stat.S_IFREG) | 0o644
            ) << 16
            archive.writestr(info, content)
    return {
        "distribution": distribution,
        "version": version,
        "filename": filename,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dependencies": list(dependencies),
        "requires_dist": requires,
    }


def member(artifact, member_id="algorithm", *, publisher="alice", **updates):
    result = {
        "id": f"{publisher}.{member_id}",
        "local_id": member_id,
        "publisher": publisher,
        "version": artifact["version"],
        "summary": "Rank documents using a tiny algorithm",
        "kind": "algorithm",
        "effects": [],
        "provides": {"id": "ranking/v1", "version": "1"},
        "distribution": artifact["distribution"],
        "wheel": artifact["filename"],
        "sha256": artifact["sha256"],
        "import_module": artifact["distribution"].replace("-", "_"),
        "export": "value",
    }
    result.update(updates)
    return result


def publication(
    directory,
    artifacts,
    members=None,
    *,
    family="mari",
    version="1.0",
    publisher="alice",
):
    index = {
        "schema_version": 1,
        "publisher": publisher,
        "family": {
            "name": family,
            "version": version,
            "description": "Knowledge algorithms",
            "context": {"solves": ["graph retrieval"]},
        },
        "artifacts": artifacts,
        "members": members if members is not None else [member(artifacts[-1])],
    }
    path = directory / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def reseal(lock):
    lock["sha256"] = hashlib.sha256(
        canonical_bytes({key: value for key, value in lock.items() if key != "sha256"})
    ).hexdigest()
    return lock


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.build = self.root / "build"
        self.build.mkdir()
        self.registry = Registry(self.root / "registry")

    def publish_chain(self):
        leaf = wheel(self.build, "test-leaf", external=["numpy>=1.26"])
        branch = wheel(self.build, "test-branch", dependencies=["test-leaf"])
        root = wheel(self.build, "test-root", dependencies=["test-branch"])
        unrelated = wheel(self.build, "test-unrelated", external=["huge-library>=9"])
        self.registry.publish(
            publication(self.build, [leaf, branch, root, unrelated], [member(root)])
        )
        return leaf, branch, root, unrelated

    def test_exact_transitive_closure_materializes_without_unrelated_artifacts(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm", allowed_effects=[])
        self.assertEqual(
            [a["distribution"] for a in lock["artifacts"]],
            ["test-branch", "test-leaf", "test-root"],
        )
        self.assertEqual(lock["external_requirements"], ["numpy>=1.26"])
        self.assertFalse(lock["environment"]["locked"])
        self.assertNotIn(str(self.root), json.dumps(lock))
        destination = self.root / "installed"
        result = self.registry.materialize(lock, destination)
        self.assertEqual(result["artifacts"], 3)
        self.assertTrue((destination / "test_leaf/__init__.py").is_file())
        self.assertFalse((destination / "test_unrelated").exists())
        # Idempotent replay allows byte-identical existing files.
        self.assertEqual(
            self.registry.materialize(lock, destination)["files"], result["files"]
        )

    def test_lock_replays_in_copied_registry_after_original_is_removed(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm")
        copied = self.root / "copy"
        shutil.copytree(self.registry.root, copied)
        shutil.rmtree(self.registry.root)
        shutil.rmtree(self.build)
        result = Registry(copied).materialize(lock, self.root / "portable")
        self.assertEqual(result["artifacts"], 3)

    def test_search_filters_family_contract_effects_and_paginates_without_import(self):
        marker = self.root / "code-executed"
        artifact = wheel(
            self.build,
            "untrusted-code",
            source=f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        )
        cards = [
            member(artifact, "a"),
            member(artifact, "b", effects=["network"]),
            member(artifact, "c", effects=["unknown"]),
        ]
        path = publication(self.build, [artifact], cards)
        self.registry.publish(path)
        self.registry.publish(
            publication(self.build, [artifact], [member(artifact, "z")], family="other")
        )
        self.assertEqual(
            [
                r["id"]
                for r in self.registry.search(
                    "graph retrieval", family="mari", allowed_effects=[]
                )
            ],
            ["alice.a"],
        )
        self.assertEqual(
            len(
                self.registry.search(
                    "",
                    family="mari",
                    contract="ranking/v1",
                    allowed_effects=["network"],
                )
            ),
            2,
        )
        self.assertEqual(
            [
                r["id"]
                for r in self.registry.search("", family="mari", limit=1, offset=1)
            ],
            ["alice.b"],
        )
        self.assertEqual(self.registry.search("rank", family="missing"), [])
        self.assertEqual(self.registry.search("rank", contract="missing"), [])
        self.registry.search(
            '" OR ( * : NOT ^ )'
        )  # Tokens cannot become FTS operators.
        self.registry.inspect("mari", "alice.a")
        self.registry.lock("mari", "alice.a")
        self.assertFalse(marker.exists())
        with self.assertRaisesRegex(RegistryError, "effects"):
            self.registry.lock(
                "mari", "alice.c", allowed_effects=["unknown", "network"]
            )

    def test_missing_effects_remains_unknown_under_policy(self):
        artifact = wheel(self.build, "unknown-effects")
        card = member(artifact)
        del card["effects"]
        self.registry.publish(publication(self.build, [artifact], [card]))
        self.assertEqual(self.registry.search("", allowed_effects=[]), [])
        with self.assertRaises(RegistryError):
            self.registry.lock("mari", "alice.algorithm", allowed_effects=[])

    def test_contract_index_accepts_exact_authored_references(self):
        artifact = wheel(self.build, "contracts")
        cards = [
            member(
                artifact, "extracted", provides={"id": "extracted/v1", "version": "1"}
            ),
            member(
                artifact,
                "authored",
                provides={"id": "authored/v1", "version": "1"},
            ),
        ]
        self.registry.publish(publication(self.build, [artifact], cards))
        for card_id in ("extracted", "authored"):
            self.assertEqual(
                [
                    card["id"]
                    for card in self.registry.search("", contract=f"{card_id}/v1")
                ],
                ["alice." + card_id],
            )

    def test_family_release_is_immutable_and_republication_is_idempotent(self):
        artifact = wheel(self.build, "a-wheel")
        path = publication(self.build, [artifact])
        self.assertFalse(self.registry.publish(path)["already_published"])
        self.assertTrue(self.registry.publish(path)["already_published"])
        path = publication(
            self.build, [artifact], [member(artifact, summary="Different claim")]
        )
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.publish(path)
        self.assertEqual(
            self.registry.inspect("mari", "alice.algorithm")["summary"],
            "Rank documents using a tiny algorithm",
        )

    def test_artifact_identity_is_immutable_across_family_releases(self):
        artifact = wheel(self.build, "a-wheel")
        self.registry.publish(publication(self.build, [artifact]))
        changed = wheel(self.build, "a-wheel", source="value = 'changed'\n")
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.publish(
                publication(self.build, [changed], family="new-family")
            )
        self.assertEqual(self.registry.search("", family="new-family"), [])

    def test_disjoint_members_publish_independently_under_one_family_header(self):
        first = wheel(self.build, "first-algorithm")
        second = wheel(self.build, "second-algorithm")
        self.registry.publish(
            publication(self.build, [first], [member(first, "first")])
        )
        before = self.registry.lock("mari", "alice.first")
        result = self.registry.publish(
            publication(self.build, [second], [member(second, "second")])
        )
        self.assertEqual(result["added_members"], 1)
        self.assertEqual(len(self.registry.search("", family="mari")), 2)
        self.assertEqual(self.registry.lock("mari", "alice.first"), before)
        self.assertEqual(len(self.registry.families()), 1)

    def test_member_versions_advance_independently_of_family_header(self):
        first = wheel(self.build, "versioned-algorithm")
        self.registry.publish(publication(self.build, [first]))
        before = self.registry.lock("mari", "alice.algorithm", "1.0")
        second = wheel(
            self.build, "versioned-algorithm", version="2.0", source="value = 99\n"
        )
        self.registry.publish(
            publication(self.build, [second], [member(second, version="2.0")])
        )
        latest = self.registry.lock("mari", "alice.algorithm")
        self.assertEqual(latest["version"], "2.0")
        self.assertEqual(latest["family_version"], "1.0")
        self.assertEqual(self.registry.lock("mari", "alice.algorithm", "1.0"), before)
        self.assertEqual(
            [
                card["version"]
                for card in self.registry.versions("mari", "alice.algorithm")
            ],
            ["2.0", "1.0"],
        )
        self.assertEqual(
            [card["version"] for card in self.registry.search("")], ["2.0"]
        )
        self.assertEqual(self.registry.families()[0]["version"], "1.0")

    def test_family_header_change_requires_a_new_context_version(self):
        artifact = wheel(self.build, "first")
        path = publication(self.build, [artifact])
        self.registry.publish(path)
        index = json.loads(path.read_text())
        index["family"]["description"] = "Changed context"
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "Family header"):
            self.registry.publish(path)

    def test_missing_internal_dependency_and_cycles_are_rejected(self):
        orphan = wheel(self.build, "orphan", dependencies=["missing"])
        with self.assertRaisesRegex(RegistryError, "Missing internal"):
            self.registry.publish(publication(self.build, [orphan]))
        left = wheel(self.build, "left", dependencies=["right"])
        right = wheel(self.build, "right", dependencies=["left"])
        with self.assertRaisesRegex(RegistryError, "cycle"):
            self.registry.publish(publication(self.build, [left, right]))
        self.assertEqual(self.registry.search(""), [])

    def test_requires_dist_cannot_hide_an_internal_dependency(self):
        leaf = wheel(self.build, "leaf")
        root = wheel(self.build, "root", external=["leaf==1.0"])
        with self.assertRaisesRegex(RegistryError, "disagree"):
            self.registry.publish(publication(self.build, [leaf, root]))

    def test_all_wheels_validate_before_catalog_publication(self):
        good = wheel(self.build, "good")
        bad = wheel(self.build, "bad", bad_record=True)
        with self.assertRaisesRegex(RegistryError, "RECORD mismatch"):
            self.registry.publish(publication(self.build, [good, bad]))
        self.assertEqual(self.registry.search(""), [])
        self.assertFalse((self.registry.root / "objects").exists())

    def test_tampered_cas_is_rejected_before_writing_installation(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm")
        blob = self.registry._blob(lock["artifacts"][-1]["sha256"])
        blob.write_bytes(blob.read_bytes() + b"damaged")
        destination = self.root / "install"
        with self.assertRaisesRegex(RegistryError, "hash mismatch"):
            self.registry.materialize(lock, destination)
        self.assertFalse(destination.exists())

    def test_tampered_lock_cannot_remove_closure_even_with_recomputed_hash(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm")
        lock["artifacts"] = lock["artifacts"][1:]
        with self.assertRaisesRegex(RegistryError, "Lock hash mismatch"):
            self.registry.materialize(lock, self.root / "install")
        reseal(lock)
        with self.assertRaisesRegex(RegistryError, "complete dependency closure"):
            self.registry.materialize(lock, self.root / "install")

    def test_lock_cannot_change_entrypoint_even_with_recomputed_hash(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm")
        lock["member"]["import_module"] = "os"
        lock["member"]["export"] = "system"
        reseal(lock)
        with self.assertRaisesRegex(RegistryError, "identity"):
            self.registry.materialize(lock, self.root / "install")

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for dangerous in (
            "../escape.py",
            "/escape.py",
            "a\\escape.py",
            "C:escape.py",
            "a/./escape.py",
        ):
            with self.subTest(path=dangerous):
                artifact = wheel(self.build, "dangerous", extra={dangerous: b"bad"})
                with self.assertRaisesRegex(RegistryError, "Unsafe wheel path"):
                    self.registry.publish(publication(self.build, [artifact]))
        artifact = wheel(
            self.build,
            "dangerous",
            extra={"link.py": b"../escape.py"},
            symlinks=["link.py"],
        )
        with self.assertRaisesRegex(RegistryError, "symlink"):
            self.registry.publish(publication(self.build, [artifact]))
        self.assertFalse((self.root / "escape.py").exists())

    def test_publication_filename_cannot_escape_index_directory(self):
        artifact = wheel(self.build, "dangerous")
        artifact["filename"] = "../outside.whl"
        with self.assertRaisesRegex(RegistryError, "Unsafe wheel filename"):
            self.registry.publish(publication(self.build, [artifact]))

    def test_source_artifact_symlink_is_rejected(self):
        artifact = wheel(self.build, "symlinked")
        source = self.build / artifact["filename"]
        outside = self.root / "outside.whl"
        source.rename(outside)
        source.symlink_to(outside)
        with self.assertRaisesRegex(RegistryError, "Symlink"):
            self.registry.publish(publication(self.build, [artifact]))

    def test_materialize_preflights_conflicts_and_parent_symlinks(self):
        self.publish_chain()
        lock = self.registry.lock("mari", "alice.algorithm")
        destination = self.root / "install"
        (destination / "test_root").mkdir(parents=True)
        original = destination / "test_root/__init__.py"
        original.write_text("existing code")
        with self.assertRaisesRegex(RegistryError, "conflicting"):
            self.registry.materialize(lock, destination)
        self.assertEqual(original.read_text(), "existing code")
        self.assertFalse((destination / "test_leaf").exists())
        second = self.root / "symlink-install"
        second.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (second / "test_root").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RegistryError, "Symlink"):
            self.registry.materialize(lock, second)
        self.assertEqual(list(outside.iterdir()), [])

    def test_simple_index_has_wheel_hashes_and_detached_metadata(self):
        self.publish_chain()
        destination = self.root / "simple"
        result = self.registry.export_simple(destination)
        self.assertEqual(result["projects"], 4)
        html = (destination / "test-root/index.html").read_text()
        self.assertIn("#sha256=", html)
        self.assertIn('data-core-metadata="sha256=', html)
        self.assertTrue(
            (destination / "files/test_root-1.0-py3-none-any.whl.metadata").is_file()
        )
        self.assertEqual(self.registry.export_simple(destination)["artifacts"], 4)

    def test_interface_only_publication_and_immutable_publisher_ownership(self):
        spec = {"id": "shared.records", "version": "1", "types": ["Item"]}
        path = publication(self.build, [], [], family="records")
        index = json.loads(path.read_text())
        index.pop("members")
        index.pop("artifacts")
        index["interfaces"] = [spec]
        path.write_text(json.dumps(index))
        result = self.registry.publish(path)
        self.assertEqual(result["added_interfaces"], 1)
        self.assertEqual(result["members"], result["artifacts"], 0)
        canonical = {**spec, "callables": {}}
        self.assertEqual(self.registry.interface("shared.records", "1"), canonical)
        self.assertEqual(self.registry.interfaces(), [canonical])
        self.assertEqual(self.registry.interfaces(limit=1, offset=1), [])
        self.assertEqual(self.registry.publish(path)["added_interfaces"], 0)
        index["interfaces"][0]["types"] = ["Different"]
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.publish(path)
        self.assertEqual(self.registry.interface("shared.records", "1"), canonical)
        index["family"]["name"] = "different-family"
        index["publisher"] = "bob"
        index["interfaces"][0]["version"] = "2"
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "owned by another publisher"):
            self.registry.publish(path)
        self.assertEqual(len(self.registry.families()), 1)

    def test_interface_conflict_rolls_back_members_and_other_interfaces(self):
        spec = {"id": "mari.records", "version": "1", "types": ["Item"]}
        path = publication(self.build, [], [])
        index = json.loads(path.read_text())
        index["interfaces"] = [spec]
        path.write_text(json.dumps(index))
        self.registry.publish(path)
        artifact = wheel(self.build, "fresh-member")
        path = publication(self.build, [artifact])
        index = json.loads(path.read_text())
        index["interfaces"] = [
            {"id": "mari.new", "version": "1", "types": ["New"]},
            {**spec, "types": ["Changed"]},
        ]
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "immutable"):
            self.registry.publish(path)
        self.assertEqual(self.registry.search(""), [])
        with self.assertRaisesRegex(RegistryError, "Unknown interface"):
            self.registry.interface("mari.new", "1")
        self.assertFalse(self.registry._blob(artifact["sha256"]).exists())

    def test_exact_provider_candidates_include_versions_in_pep440_order(self):
        for version in ("2.0", "1.9", "2.0rc1", "1.10"):
            artifact = wheel(self.build, "provider", version=version)
            card = member(
                artifact,
                version=version,
                contract_id="misleading-legacy-label",
                provides={"id": "shared.rank", "version": "1"},
            )
            self.registry.publish(publication(self.build, [artifact], [card]))
        self.assertEqual(
            [card["version"] for card in self.registry.candidates("shared.rank", "1")],
            ["2.0", "2.0rc1", "1.10", "1.9"],
        )
        self.assertEqual(
            [
                card["version"]
                for card in self.registry.candidates(
                    "shared.rank", "1", version_spec=">=1,<2"
                )
            ],
            ["1.10", "1.9"],
        )
        self.assertEqual(
            self.registry.candidates("shared.rank", "1", limit=1, offset=2)[0][
                "version"
            ],
            "1.10",
        )
        self.assertEqual(self.registry.candidates("shared.rank", "2"), [])
        self.assertEqual(
            self.registry.candidates("shared.rank", "1", family="other"), []
        )
        self.assertEqual(self.registry.candidates("misleading-legacy-label", "1"), [])
        with self.assertRaisesRegex(RegistryError, "specifier"):
            self.registry.candidates(
                "shared.rank", "1", version_spec="not a constraint"
            )

    def test_publishers_contribute_independently_to_one_open_family(self):
        shared = wheel(self.build, "shared-support")
        alice = wheel(self.build, "alice-operation", dependencies=["shared-support"])
        bob = wheel(self.build, "bob-operation", dependencies=["shared-support"])
        self.registry.publish(
            publication(self.build, [shared, alice], [member(alice, "solve")])
        )
        result = self.registry.publish(
            publication(
                self.build,
                [shared, bob],
                [member(bob, "solve", publisher="bob")],
                publisher="bob",
            )
        )
        self.assertEqual(result["added_members"], 1)
        self.assertEqual(
            [card["id"] for card in self.registry.search("", family="mari")],
            ["alice.solve", "bob.solve"],
        )
        self.assertEqual(len(self.registry.families()), 1)
        self.assertEqual(len(list((self.registry.root / "objects").rglob("*.whl"))), 3)
        self.assertEqual(
            self.registry.lock("mari", "bob.solve")["member"]["publisher"], "bob"
        )

    def test_unscoped_and_impersonated_publications_are_rejected(self):
        artifact = wheel(self.build, "qualified")
        path = publication(self.build, [artifact])
        index = json.loads(path.read_text())
        del index["publisher"]
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "Publisher"):
            self.registry.publish(path)
        index["publisher"] = "bob"
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, "Publisher-qualified"):
            self.registry.publish(path)
        self.assertEqual(self.registry.families(), [])

    def test_older_database_schemas_are_rejected_without_migration(self):
        root = self.root / "old-repository"
        root.mkdir()
        with sqlite3.connect(root / "registry.sqlite3") as database:
            database.execute("CREATE TABLE releases (family TEXT)")
        with self.assertRaisesRegex(RegistryError, "create a fresh repository"):
            Registry(root)

    def test_distribution_versions_belong_to_first_publisher_but_exact_reuse_is_allowed(
        self,
    ):
        first = wheel(self.build, "owned-distribution")
        self.registry.publish(publication(self.build, [first]))
        self.registry.publish(
            publication(
                self.build,
                [first],
                [member(first, "reuse", publisher="bob")],
                publisher="bob",
            )
        )
        changed = wheel(
            self.build, "owned-distribution", version="2.0", source="value = 99\n"
        )
        with self.assertRaisesRegex(
            RegistryError, "distribution is owned by another publisher"
        ):
            self.registry.publish(
                publication(
                    self.build,
                    [changed],
                    [member(changed, "replace", publisher="bob")],
                    publisher="bob",
                )
            )
        self.assertEqual(
            self.registry.inspect("mari", "bob.reuse")["sha256"], first["sha256"]
        )
        with self.assertRaisesRegex(RegistryError, "Unknown member"):
            self.registry.inspect("mari", "bob.replace")


if __name__ == "__main__":
    unittest.main()
