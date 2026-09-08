import inspect
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from module_families.catalog import Catalog, ManifestError
from module_families.contracts import ContractError, Signature
from module_families.interfaces import (
    InterfaceError,
    signature_from_spec,
    validate_interface,
    validate_interface_reference,
)
from module_families.manifest import ManifestFormatError, load_manifest, write_manifest


def parameter(name, kind="POSITIONAL_OR_KEYWORD", required=True):
    return {"name": name, "kind": kind, "required": required}


def interface():
    return {
        "id": "example.operation",
        "version": "1",
        "callables": {"run": {"parameters": [parameter("value")]}},
        "types": ["Result"],
    }


class InterfaceTests(unittest.TestCase):
    def test_runtime_metadata_roundtrips_every_supported_parameter_kind(self):
        def prototype(a, /, b=0, *items, flag, optional=None, **extras):
            raise AssertionError("a prototype must never execute")

        original = Signature("shape", "1", {"run": prototype}, types=("Zulu", "Alpha"))
        reconstructed = signature_from_spec(original.metadata())
        self.assertEqual(reconstructed.metadata(), original.metadata())
        self.assertEqual(validate_interface(original.metadata()), original.metadata())
        kinds = [
            p.kind for p in reconstructed.callables["run"].signature.parameters.values()
        ]
        self.assertIn(inspect.Parameter.VAR_KEYWORD, kinds)

    def test_declaration_builds_real_checks_without_invoking_the_provider(self):
        calls = []

        def compatible(value, *, optional=None):
            calls.append(value)

        def incompatible(value, required_extra):
            calls.append(value)

        class Result:
            pass

        signature = signature_from_spec(interface())
        module = signature.seal({"run": compatible, "Result": Result})
        self.assertIs(module.Result, Result)
        with self.assertRaises(ContractError):
            signature.seal({"run": incompatible, "Result": Result})
        self.assertEqual(calls, [])

    def test_async_category_is_retained_and_checked(self):
        async def prototype(value):
            raise AssertionError("not executed")

        def synchronous(value):
            raise AssertionError("not executed")

        original = Signature("async", "1", {"run": prototype})
        reconstructed = signature_from_spec(original.metadata())
        reconstructed.seal({"run": prototype})
        with self.assertRaisesRegex(ContractError, "must be async"):
            reconstructed.seal({"run": synchronous})

    def test_normalization_fills_only_structural_defaults_and_returns_fresh_data(self):
        spec = {"id": "empty", "version": "1", "callables": {"ping": {}}}
        normalized = validate_interface(spec)
        self.assertEqual(normalized["types"], [])
        self.assertEqual(
            normalized["callables"]["ping"], {"parameters": [], "asynchronous": False}
        )
        normalized["callables"]["ping"]["parameters"].append(parameter("other"))
        self.assertEqual(spec["callables"]["ping"], {})
        self.assertEqual(
            validate_interface_reference({"id": "example", "version": "1"}),
            {"id": "example", "version": "1"},
        )

    def test_invalid_interface_syntax_and_impossible_call_shapes_are_rejected(self):
        invalid = []
        invalid.append(
            {"id": "example", "version": "1", "annotations": "__import__('candidate')"}
        )
        invalid.append({"id": " example", "version": "1"})
        invalid.append({"id": "example", "version": 1})
        invalid.append({"id": "example", "version": "1", "types": ["Result", "Result"]})
        invalid.append(
            {
                "id": "example",
                "version": "1",
                "types": ["run"],
                "callables": {"run": {}},
            }
        )
        invalid.append(
            {"id": "example", "version": "1", "callables": {"run": {"asynchronous": 1}}}
        )
        bad_parameters = [
            [parameter("x"), parameter("x")],
            [parameter("x", "KEYWORD_ONLY"), parameter("y")],
            [parameter("x", required=False), parameter("y")],
            [parameter("x", "NOT_A_KIND")],
            [parameter("args", "VAR_POSITIONAL", True)],
            [
                parameter("args", "VAR_POSITIONAL", False),
                parameter("other", "VAR_POSITIONAL", False),
            ],
            [
                parameter("kwargs", "VAR_KEYWORD", False),
                parameter("other", "VAR_KEYWORD", False),
            ],
            [parameter("class")],
            [{"name": "x", "kind": "POSITIONAL_ONLY", "required": 1}],
            [{**parameter("x"), "default": "__import__('candidate')"}],
        ]
        invalid.extend(
            {"id": "example", "version": "1", "callables": {"run": {"parameters": ps}}}
            for ps in bad_parameters
        )
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(InterfaceError):
                signature_from_spec(spec)


class InterfaceManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mf-interfaces-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "family.toml"
        self.document = {
            "schema_version": 1,
            "publisher": {"name": "example"},
            "family": {
                "name": "example",
                "version": "0.1.0",
                "description": "Example family",
            },
        }

    def source(self, package="sample", relative="src"):
        directory = self.root / relative / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
        (directory / "ops.py").write_text(
            "class Result:\n    pass\ndef run(value):\n    return value\n"
        )
        return directory

    def load(self):
        write_manifest(self.document, self.path)
        return Catalog.load(self.path)

    def test_interface_only_toml_needs_no_source_tree(self):
        self.document["interfaces"] = [interface()]
        catalog = self.load()
        self.assertEqual(catalog.document["members"], [])
        self.assertNotIn("source", catalog.document)
        self.assertEqual(
            catalog.document["interfaces"], [validate_interface(interface())]
        )
        self.assertEqual(catalog.search(), [])
        text = self.path.read_text()
        self.assertIn("[[interfaces]]", text)
        self.assertNotIn("[source]", text)

    def test_interfaces_can_be_contributed_locally_without_root_inventory(self):
        members = self.root / "members"
        members.mkdir()
        (members / "types.toml").write_text(
            '[[interfaces]]\nid = "example.types"\nversion = "1"\ntypes = ["Result"]\n'
        )
        self.document["contributions"] = {"include": ["members/*.toml"]}
        catalog = self.load()
        self.assertEqual(catalog.document["interfaces"][0]["types"], ["Result"])
        self.assertEqual(catalog.document["members"], [])

    def test_duplicate_interface_reference_reports_both_origins(self):
        self.document["interfaces"] = [interface()]
        fragment = self.root / "duplicate.toml"
        fragment.write_text('[[interfaces]]\nid = "example.operation"\nversion = "1"\n')
        self.document["contributions"] = {"include": ["duplicate.toml"]}
        with self.assertRaises(ManifestFormatError) as raised:
            self.load()
        self.assertIn(str(fragment), str(raised.exception))
        self.assertIn(str(self.path), str(raised.exception))

    def test_grouped_module_exports_do_not_need_a_single_symbol(self):
        self.source()
        self.document["source"] = {"root": "src", "package": "sample"}
        self.document["interfaces"] = [interface()]
        self.document["members"] = [
            {
                "id": "operation",
                "kind": "module",
                "exports": {"Result": "sample.ops:Result", "run": "sample.ops:run"},
                "provides": {"id": "example.operation", "version": "1"},
            }
        ]
        card = self.load().inspect("example.operation")
        self.assertNotIn("symbol", card)
        self.assertEqual(card["exports"]["Result"], "sample.ops:Result")
        self.document["members"][0]["exports"]["Result"] = "outside:Result"
        with self.assertRaisesRegex(ManifestFormatError, "outside"):
            self.load()

    def test_exact_contract_references_and_sharing_are_schema_checked(self):
        self.source()
        self.document["source"] = {"root": "src", "package": "sample"}
        self.document["members"] = [{"id": "run", "symbol": "sample.ops:run"}]
        self.assertNotIn("provides", self.load().inspect("example.run"))
        baseline = deepcopy(self.document["members"][0])
        malformed = [
            {"contract_id": "legacy/v1"},
            {"contract": "legacy/v1"},
            {"provides": "example.operation"},
            {"provides": {"id": "example.operation"}},
            {
                "requires": {
                    "processor": {
                        "id": "example.operation",
                        "version": "1",
                        "range": "*",
                    }
                }
            },
            {"requires": {"not.a.slot": {"id": "example.operation", "version": "1"}}},
            {"sharing": [["left.Result", "right.Result"]]},
        ]
        for values in malformed:
            self.document["members"] = [{**baseline, **values}]
            with self.subTest(values=values), self.assertRaises(ManifestFormatError):
                self.load()
        self.document["members"] = [
            {
                **baseline,
                "requires": {
                    slot: {"id": "types", "version": "1"} for slot in ("left", "right")
                },
                "sharing": [["left.Result", "right.Result"]],
            }
        ]
        self.assertEqual(len(self.load().inspect("example.run")["requires"]), 2)

    def test_multiple_sources_have_stable_explicit_alias_prefixes(self):
        self.source("left_package", "left-src")
        self.source("right_package", "right-src")
        self.document["sources"] = {
            "left": {"root": "left-src", "package": "left_package"},
        }
        self.document["discovery"] = {"public": True}
        first = self.load().document["members"]
        self.assertEqual(
            {member["id"] for member in first},
            {"example.left.ops.Result", "example.left.ops.run"},
        )
        self.document["sources"]["right"] = {
            "root": "right-src",
            "package": "right_package",
        }
        catalog = self.load()
        self.assertEqual(
            [
                member
                for member in catalog.document["members"]
                if member["id"].startswith("example.left.")
            ],
            first,
        )
        ids = {member["id"] for member in catalog.document["members"]}
        self.assertEqual(
            ids,
            {
                "example.left.ops.Result",
                "example.left.ops.run",
                "example.right.ops.Result",
                "example.right.ops.run",
            },
        )
        self.document["members"] = [
            {
                "id": "combined",
                "kind": "module",
                "exports": {
                    "Result": "left_package.ops:Result",
                    "run": "right_package.ops:run",
                },
            }
        ]
        self.assertEqual(len(self.load().inspect("example.combined")["exports"]), 2)

    def test_module_projection_limits_discovery_before_parsing_other_source(self):
        package = self.source()
        (package / "unsupported.py").write_text("def broken(\n")
        self.document["source"] = {
            "root": "src",
            "package": "sample",
            "modules": ["sample.ops"],
        }
        self.document["discovery"] = {"public": True}
        self.assertEqual(
            {m["id"] for m in self.load().document["members"]},
            {"example.ops.Result", "example.ops.run"},
        )
        for modules in (
            ["sample.ops", "sample.ops"],
            ["other.ops"],
            [],
            ["sample.not-valid"],
        ):
            self.document["source"]["modules"] = modules
            with self.subTest(modules=modules), self.assertRaises(ManifestFormatError):
                self.load()

    def test_source_ambiguity_and_empty_families_are_rejected(self):
        with self.assertRaises(ManifestFormatError):
            self.load()
        self.document["interfaces"] = [interface()]
        self.document["source"] = {"root": "one", "package": "package"}
        self.document["sources"] = {"another": {"root": "two", "package": "other"}}
        with self.assertRaisesRegex(ManifestFormatError, "mutually exclusive"):
            self.load()
        document = {
            "schema_version": 1,
            "name": "example",
            "publisher": "example",
            "version": "0.1.0",
            "description": "Example",
            "context": {},
            "members": [],
            "interfaces": [interface()],
            "sources": {
                "one": {"root": "one", "package": "package"},
                "two": {"root": "two", "package": "package.nested"},
            },
        }
        with self.assertRaisesRegex(ManifestError, "overlapping"):
            Catalog(document)

    def test_loading_declarations_never_executes_package_initialization(self):
        package = self.source()
        marker = self.root / "executed"
        (package / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        )
        self.document["source"] = {"root": "src", "package": "sample"}
        self.document["discovery"] = {"public": True}
        self.document["interfaces"] = [interface()]
        load_manifest(write_manifest(self.document, self.path))
        self.assertFalse(marker.exists())

    def test_publisher_qualifies_local_ids_once_and_preserves_global_interfaces(self):
        self.source()
        self.document["source"] = {"root": "src", "package": "sample"}
        self.document["discovery"] = {"public": True}
        self.document["publisher"] = {"name": "alice"}
        self.document["interfaces"] = [interface()]
        self.document["members"] = [
            {
                "id": "alice.ops.run",
                "provides": {"id": "example.operation", "version": "1"},
            }
        ]
        catalog = self.load()
        card = catalog.inspect("alice.ops.run")
        self.assertEqual((card["publisher"], card["local_id"]), ("alice", "ops.run"))
        self.assertEqual(card["provides"]["id"], "example.operation")
        self.assertEqual(catalog.document["publisher"], "alice")
        write_manifest(catalog.document, self.path)
        self.assertEqual(Catalog.load(self.path).document, catalog.document)

    def test_publisher_alias_duplicates_and_member_impersonation_are_rejected(self):
        self.source()
        self.document["source"] = {"root": "src", "package": "sample"}
        self.document["discovery"] = {"public": True}
        self.document["publisher"] = {"name": "alice"}
        self.document["members"] = [{"id": "ops.run"}, {"id": "alice.ops.run"}]
        with self.assertRaisesRegex(ManifestFormatError, "duplicate explicit"):
            self.load()
        self.document["members"] = [{"id": "ops.run", "publisher": "bob"}]
        with self.assertRaisesRegex(ManifestFormatError, "impersonate"):
            self.load()
        self.document["members"] = [{"id": "ops.run", "local_id": "another"}]
        with self.assertRaisesRegex(ManifestFormatError, "contradicts"):
            self.load()

    def test_normalized_publisher_metadata_cannot_contradict_card_ownership(self):
        self.document["interfaces"] = [interface()]
        self.document["publisher"] = {"name": "alice"}
        document = self.load().document
        document["source"] = {"root": "src", "package": "sample"}
        document["members"] = [
            {
                "id": "bob.run",
                "publisher": "alice",
                "local_id": "run",
                "symbol": "sample:run",
                "kind": "algorithm",
                "summary": "An operation",
            }
        ]
        with self.assertRaisesRegex(ManifestError, "must agree"):
            Catalog(document)


if __name__ == "__main__":
    unittest.main()
