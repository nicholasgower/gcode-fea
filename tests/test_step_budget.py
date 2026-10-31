from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "helper_scripts"))

import step_budget  # noqa: E402
from gcode_fea.cache import load_probe  # noqa: E402
from gcode_fea.step_planar import estimate_faces  # noqa: E402

PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"
CUBE = REPO_ROOT / "tests" / "fixtures" / "phase1" / "cube20_probe.json"
HAVE_MANIFOLD = importlib.util.find_spec("manifold3d") is not None


class StepBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_probe(PROBE)

    def test_planar_matches_face_estimate(self) -> None:
        report = step_budget.estimate(
            self.model, engine="planar", tolerance=0.005, limit=150_000
        )
        self.assertEqual(report["engine"], "planar")
        self.assertEqual(report["unit"], "faces")
        self.assertEqual(
            report["units"],
            estimate_faces(self.model, 1, 0.005),
        )
        self.assertFalse(report["exceeds_limit"])
        self.assertEqual(report["dropped_solids"], 0)
        self.assertGreater(report["estimated_bytes"], 0)

    def test_layer_range_recommendation(self) -> None:
        model = load_probe(CUBE).filter_roi(layer_range=(0, 4))
        unlimited = step_budget.estimate(
            model, engine="planar", tolerance=0.005, limit=10_000_000
        )
        self.assertEqual(unlimited["layer_count"], 5)
        limit = max(1, int(unlimited["units_per_layer"] * 2.5))
        report = step_budget.estimate(
            model, engine="planar", tolerance=0.005, limit=limit
        )
        self.assertTrue(report["exceeds_limit"])
        self.assertIsNotNone(report["recommended_layer_range"])
        start, end = report["recommended_layer_range"]
        self.assertTrue(start <= end)
        self.assertLess(end, start + 5)

    def test_invalid_engine(self) -> None:
        with self.assertRaises(ValueError):
            step_budget.estimate(self.model, engine="nope")

    def test_cli_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "budget.json"
            code = step_budget.main(
                [
                    "--probe",
                    str(PROBE),
                    "--engine",
                    "planar",
                    "--tolerance",
                    "0.005",
                    "--json",
                    str(target),
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(target.read_text())
            self.assertEqual(payload["engine"], "planar")
            self.assertGreater(payload["units"], 0)

    @unittest.skipUnless(HAVE_MANIFOLD, "manifold3d is not installed")
    def test_merged_counts(self) -> None:
        report = step_budget.estimate(
            self.model, engine="merged", tolerance=0.005, limit=60_000
        )
        self.assertEqual(report["engine"], "merged")
        self.assertEqual(report["unit"], "triangles")
        self.assertEqual(report["solids"], 1)
        self.assertGreater(report["units"], 0)
        self.assertEqual(report["dropped_solids"], 0)
        self.assertFalse(report["exceeds_limit"])


if __name__ == "__main__":
    unittest.main()
