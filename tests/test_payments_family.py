"""A second domain exercises the generic publishing path in a fresh interpreter."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class PublishedPaymentsTests(unittest.TestCase):
    def test_independent_family_publishes_and_composes_without_source_imports(self):
        with TemporaryDirectory(prefix="payments-family-test-") as temporary:
            work = Path(temporary)
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples/published_payments.py"),
                    "--work-dir",
                    str(work),
                ],
                cwd=work,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            report = json.loads((work / "report.json").read_text())
            self.assertEqual(report["processor_candidates"], 2)
            self.assertTrue(report["shared_money_identity"])
            self.assertTrue(report["shared_receipt_identity"])
            self.assertFalse(report["loaded_original_package"])
            self.assertEqual(report["external_requirements"], [])
            self.assertIn(
                "Rejected another contract in the same family", process.stdout
            )
            index = json.loads((work / "wheels/index.json").read_text())
            self.assertEqual(index["family"]["name"], "payments")
            self.assertEqual(len(index["members"]), 15)
            composition = report["composition"]
            self.assertEqual(composition["calls"], 20)
            self.assertEqual(composition["attempts"], 2)
            self.assertEqual(composition["charge_effects"], 1)
            self.assertEqual(composition["ledger_entries"], 1)
            self.assertTrue(composition["same_receipt_object"])
            order = report["composition_order"]
            ledger_outside = order["ledger_outside"]
            self.assertEqual(ledger_outside["calls"], 20)
            self.assertEqual(ledger_outside["attempts"], 2)
            self.assertEqual(ledger_outside["charge_effects"], 1)
            self.assertEqual(ledger_outside["ledger_entries"], 20)
            self.assertTrue(ledger_outside["same_receipt_object"])
            self.assertTrue(order["same_bundle_identity"])
            self.assertTrue(order["different_composition_identities"])
            self.assertTrue(order["fresh_instances"])
            self.assertNotEqual(composition["recipe"], ledger_outside["recipe"])
            lock = json.loads((work / "locks/processors.alpha.json").read_text())
            self.assertLess(len(lock["artifacts"]), len(index["artifacts"]))
            self.assertFalse(
                any(
                    "beta" in artifact["distribution"] for artifact in lock["artifacts"]
                )
            )
            self.assertEqual(lock["external_requirements"], [])


if __name__ == "__main__":
    unittest.main()
