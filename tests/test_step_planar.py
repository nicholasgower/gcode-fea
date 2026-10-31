from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cache import load_probe
from gcode_fea.config import PluginConfig
from gcode_fea.export import export_step
from gcode_fea.plugin_export import export_from_probe, export_step_from_cache
from gcode_fea.step_planar import estimate_faces, write_step_planar

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


class PlanarStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_probe(PROBE)

    def test_write_and_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            result = write_step_planar(self.model, path, profile_stride=1)
            self.assertEqual(result["engine"], "planar")
            self.assertEqual(result["solids"], 61)
            self.assertGreater(result["faces"], 0)
            self.assertLess(result["volume_relative_error"], 0.01)
            self.assertAlmostEqual(
                result["volume"] / self.model.volume, 1.0, delta=0.01
            )
            text = path.read_text()
            self.assertTrue(text.startswith("ISO-10303-21;"))
            self.assertIn("MANIFOLD_SOLID_BREP", text)
            self.assertIn("ADVANCED_FACE", text)
            self.assertIn("CLOSED_SHELL", text)
            self.assertIn("EDGE_CURVE", text)
            self.assertIn("ADVANCED_BREP_SHAPE_REPRESENTATION", text)

    def test_entity_references_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            write_step_planar(self.model, path, profile_stride=4)
            text = path.read_text()
            definitions = re.findall(r"^#(\d+) =", text, flags=re.MULTILINE)
            self.assertEqual(len(definitions), len(set(definitions)))
            defined = {int(value) for value in definitions}
            referenced = {
                int(value)
                for value in re.findall(r"#(\d+)", text)
            }
            self.assertTrue(referenced)
            self.assertEqual(referenced - defined, set())

    def test_stride_reduces_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            full = write_step_planar(
                self.model, Path(tmp) / "full.step", profile_stride=1
            )
            coarse = write_step_planar(
                self.model, Path(tmp) / "coarse.step", profile_stride=2
            )
            self.assertLess(coarse["faces"], full["faces"])
            self.assertLess(coarse["points"], full["points"])
            self.assertGreater(
                coarse["volume_relative_error"], full["volume_relative_error"]
            )

    def test_face_estimate_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for stride in (1, 2, 4):
                result = write_step_planar(
                    self.model,
                    Path(tmp) / f"beads_{stride}.step",
                    profile_stride=stride,
                )
                self.assertEqual(
                    estimate_faces(self.model, stride), result["faces"]
                )

    def test_export_engine_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = export_step(
                self.model,
                Path(tmp) / "beads.step",
                step_engine="planar",
            )
            self.assertEqual(result["engine"], "planar")
            self.assertEqual(result["segment_prisms"], 61)
            self.assertIn("bbox", result)

    def test_auto_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = export_step(
                self.model, Path(tmp) / "beads.step", step_engine="auto"
            )
            self.assertIn(result["engine"], ("gmsh", "planar"))

    def test_gmsh_engine_requires_gmsh(self) -> None:
        if importlib.util.find_spec("gmsh") is not None:
            self.skipTest("gmsh is installed; the missing-dependency path is not exercised")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                export_step(
                    self.model,
                    Path(tmp) / "beads.step",
                    step_engine="gmsh",
                )

    def test_unknown_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                export_step(
                    self.model,
                    Path(tmp) / "beads.step",
                    step_engine="nope",
                )


class StepOnlyExportTests(unittest.TestCase):
    def test_export_step_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            export_from_probe(PROBE, out, PluginConfig(resolution=0.2))
            target = Path(tmp) / "view.step"
            summary = export_step_from_cache(
                out / "slice_cache.json",
                target,
                PluginConfig(
                    step_engine="planar",
                    step_stride=2,
                    max_step_faces=100_000,
                ),
            )
            self.assertEqual(summary["engine"], "planar")
            self.assertEqual(summary["bead_count"], 3)
            self.assertGreater(summary["solids"], 0)
            self.assertGreater(summary["faces"], 0)
            self.assertGreater(summary["bytes"], 0)
            self.assertTrue(target.exists())
            self.assertLess(summary["volume_relative_error"], 0.05)

    def test_step_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            export_from_probe(PROBE, out, PluginConfig(resolution=0.2))
            with self.assertRaises(ValueError):
                export_step_from_cache(
                    out / "slice_cache.json",
                    Path(tmp) / "view.step",
                    PluginConfig(max_step_faces=5),
                )


class PluginStepExportTests(unittest.TestCase):
    def test_plugin_export_with_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            summary = export_from_probe(
                PROBE,
                out,
                PluginConfig(
                    resolution=0.2,
                    write_step=True,
                    step_engine="planar",
                    step_stride=2,
                ),
            )
            step = Path(summary["files"]["step"])
            self.assertTrue(step.exists())
            text = step.read_text()
            self.assertTrue(text.startswith("ISO-10303-21;"))
            self.assertIn("MANIFOLD_SOLID_BREP", text)

    def test_config_validates_engine(self) -> None:
        with self.assertRaises(ValueError):
            PluginConfig(step_engine="nope")
        with self.assertRaises(ValueError):
            PluginConfig(max_step_faces=0)

    def test_face_limit_blocks_oversized_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as context:
                export_from_probe(
                    PROBE,
                    Path(tmp) / "out",
                    PluginConfig(
                        resolution=0.2,
                        write_step=True,
                        step_engine="planar",
                        max_step_faces=10,
                    ),
                )
            self.assertIn("preflight", str(context.exception))


if __name__ == "__main__":
    unittest.main()
