from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cache import load_probe
from gcode_fea.contacts import contact_stats, detect_contacts
from gcode_fea.report import build_report
from gcode_fea.sweep import mesh_stats

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase1" / "cube20_probe.json"
REPORT = REPO_ROOT / "tests" / "fixtures" / "phase1" / "cube20_report.json"


class Phase1Cube20Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_probe(PROBE)
        cls.contacts = detect_contacts(cls.model, resolution=0.2)
        cls.mesh = mesh_stats(cls.model)
        cls.report = build_report(cls.model, cls.contacts, 0.2, cls.mesh)

    def test_shape(self) -> None:
        self.assertEqual(len(self.model.beads), 390)
        self.assertEqual(len(self.model.layers()), 100)
        self.assertEqual(self.model.meta["layer_count"], 100)
        lo = self.model.bbox()
        self.assertAlmostEqual(lo[0], -9.9966, delta=1e-3)
        self.assertAlmostEqual(lo[3], 9.9966, delta=1e-3)
        self.assertAlmostEqual(lo[5], 20.0, delta=1e-6)

    def test_volume_and_watertight(self) -> None:
        self.assertAlmostEqual(self.report["summary"]["analytic_volume"], 3171.1236, delta=1e-3)
        self.assertLess(self.report["summary"]["volume_relative_error"], 0.01)
        self.assertEqual(self.report["summary"]["watertight_fraction"], 1.0)

    def test_contacts(self) -> None:
        stats = contact_stats(self.contacts, 0.2, 0.45)
        self.assertEqual(stats["count"], 789)
        self.assertEqual(stats["intralayer_count"], 386)
        self.assertEqual(stats["interlayer_count"], 403)
        self.assertGreater(stats["interlayer_area"], stats["intralayer_area"])

    def test_fixture_report_matches(self) -> None:
        fixture = json.loads(REPORT.read_text())
        self.assertEqual(fixture["summary"]["bead_count"], 390)
        self.assertEqual(fixture["summary"]["contact_count"], 789)
        self.assertAlmostEqual(
            fixture["summary"]["analytic_volume"],
            self.report["summary"]["analytic_volume"],
            places=6,
        )


if __name__ == "__main__":
    unittest.main()
