from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HAVE_MANIFOLD = importlib.util.find_spec("manifold3d") is not None

from gcode_fea.cache import load_probe
from gcode_fea.export import export_step
from gcode_fea.merged_step import write_step_merged

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


@unittest.skipUnless(HAVE_MANIFOLD, "manifold3d is not installed")
class MergedStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_probe(PROBE)

    def test_union_merges_overlapping_beads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = write_step_merged(
                self.model,
                Path(tmp) / "merged.step",
                tolerance=0.005,
                max_triangles=60_000,
            )
            self.assertEqual(result["engine"], "merged")
            self.assertEqual(result["solids"], 1)
            self.assertGreater(result["triangles"], 0)
            self.assertLess(result["volume"], self.model.volume)
            self.assertGreater(result["volume"], 0.85 * self.model.volume)
            text = (Path(tmp) / "merged.step").read_text()
            self.assertIn("MANIFOLD_SOLID_BREP", text)
            self.assertIn("ADVANCED_FACE", text)
            self.assertNotIn("FACETED_BREP", text)

    def test_export_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = export_step(
                self.model,
                Path(tmp) / "merged.step",
                step_engine="merged",
                step_tolerance=0.01,
            )
            self.assertEqual(result["engine"], "merged")
            self.assertGreaterEqual(result["solids"], 1)

    def test_triangle_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_step_merged(
                    self.model,
                    Path(tmp) / "merged.step",
                    tolerance=0.005,
                    max_triangles=10,
                )


if __name__ == "__main__":
    unittest.main()
