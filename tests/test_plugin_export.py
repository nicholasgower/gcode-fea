from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.config import PluginConfig
from gcode_fea.plugin_export import export_from_cache, export_from_probe

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"
GCODE = REPO_ROOT / "tests" / "fixtures" / "process" / "cube20_first2layers.gcode"


class PluginExportTests(unittest.TestCase):
    def test_probe_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            stages: list[tuple[float, str]] = []
            summary = export_from_probe(
                PROBE,
                out,
                PluginConfig(resolution=0.2),
                progress=lambda fraction, message: stages.append(
                    (fraction, message)
                ),
            )
            self.assertEqual(summary["bead_count"], 3)
            self.assertEqual(summary["layer_count"], 1)
            self.assertEqual(summary["contacts"], 2)
            self.assertGreater(summary["volume"], 0.0)
            self.assertTrue(Path(summary["files"]["sidecar"]).exists())
            self.assertTrue(Path(summary["files"]["report"]).exists())
            self.assertGreaterEqual(len(stages), 3)
            self.assertEqual(stages[-1][0], 1.0)

            sidecar = json.loads(
                Path(summary["files"]["sidecar"]).read_text()
            )
            self.assertEqual(len(sidecar["beads"]), 3)

    def test_cache_export_with_gcode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            export_from_probe(PROBE, out, PluginConfig(resolution=0.2))
            summary = export_from_cache(
                out / "slice_cache.json",
                out,
                PluginConfig(resolution=0.2),
                gcode_path=GCODE,
            )
            self.assertIsNotNone(summary["interfaces"])
            self.assertIsNotNone(summary["mean_bond_quality"])
            sidecar = json.loads(
                Path(summary["files"]["sidecar"]).read_text()
            )
            self.assertIn("interfaces", sidecar)
            self.assertIn("adhesion", sidecar)

    def test_missing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as context:
                export_from_cache(
                    Path(tmp) / "missing.json", Path(tmp) / "out"
                )
            self.assertIn("slice cache", str(context.exception))

    def test_empty_roi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            config = PluginConfig(
                resolution=0.2,
                roi=(100.0, 100.0, 100.0, 101.0, 101.0, 101.0),
            )
            with self.assertRaises(ValueError) as context:
                export_from_probe(PROBE, out, config)
            self.assertIn("no beads", str(context.exception))

    def test_stl_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            summary = export_from_probe(
                PROBE,
                out,
                PluginConfig(resolution=0.2, write_stl=True),
            )
            stl = Path(summary["files"]["stl"])
            self.assertTrue(stl.exists())
            self.assertGreater(stl.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
