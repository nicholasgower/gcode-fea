from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HAVE_MANIFOLD = importlib.util.find_spec("manifold3d") is not None

from gcode_fea.cache import load_probe
from gcode_fea.config import PluginConfig
from gcode_fea.export import export_step
from gcode_fea.merged_step import write_step_merged
from gcode_fea.plugin_export import export_step_from_cache

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
            self.assertTrue(result["closed"])
            self.assertEqual(result["open_edges"], 0)
            self.assertEqual(result["dropped_solids"], 0)
            self.assertGreater(result["bytes"], 0)
            text = (Path(tmp) / "merged.step").read_text()
            self.assertIn("MANIFOLD_SOLID_BREP", text)
            self.assertIn("ADVANCED_FACE", text)
            self.assertNotIn("FACETED_BREP", text)

    def test_multilayer_cube_is_one_closed_body(self) -> None:
        cube = load_probe(
            REPO_ROOT / "tests" / "fixtures" / "phase1" / "cube20_probe.json"
        ).filter_roi(layer_range=(0, 1))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cube2.step"
            result = write_step_merged(
                cube,
                target,
                tolerance=0.005,
                max_triangles=100_000,
            )
            self.assertEqual(result["solids"], 1)
            self.assertTrue(result["closed"])
            self.assertEqual(result["open_edges"], 0)
            text = target.read_text()
            self.assertEqual(
                len(re.findall(r"= MANIFOLD_SOLID_BREP\(", text)), 1
            )
            self.assertEqual(len(re.findall(r"= CLOSED_SHELL\(", text)), 1)

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

    def test_coarse_profile_fails_instead_of_dropping(self) -> None:
        profile_len = len(self.model.beads[0].cross_section.profile)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as context:
                write_step_merged(
                    self.model,
                    Path(tmp) / "merged.step",
                    profile_stride=profile_len + 1,
                )
            self.assertIn("dropped", str(context.exception))

    def test_plugin_step_forwards_triangle_limit(self) -> None:
        from gcode_fea.cache import save_cache

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "slice.json"
            save_cache(self.model, cache)
            with self.assertRaises(ValueError) as context:
                export_step_from_cache(
                    cache,
                    Path(tmp) / "view.step",
                    PluginConfig(
                        step_engine="merged",
                        step_tolerance=0.005,
                        max_step_triangles=10,
                    ),
                )
            self.assertIn("triangles", str(context.exception))


if __name__ == "__main__":
    unittest.main()
