from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cache import load_probe
from gcode_fea.cross_section import solve_cross_section
from gcode_fea.model import Bead
from gcode_fea.sweep import mesh_bead, mesh_stats

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


def make_bead(points, area=0.08, height=0.2):
    cs = solve_cross_section(area, height, "volume_conserving", 0.45)
    return Bead("b", 0, 0, height, "test", tuple(points), 0.45, height, area, cs)


class SweepTests(unittest.TestCase):
    def test_straight_prism_volume_and_watertight(self) -> None:
        bead = make_bead(((0, 0, 0), (10, 0, 0)))
        mesh = mesh_bead(bead)
        self.assertAlmostEqual(mesh.volume / bead.volume, 1.0, delta=1e-3)
        self.assertTrue(mesh.watertight()[0])
        self.assertEqual(mesh.bbox[2:3], (0.0,))
        self.assertAlmostEqual(mesh.bbox[5], 0.2, places=6)

    def test_closed_loop(self) -> None:
        bead = make_bead(
            ((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0))
        )
        mesh = mesh_bead(bead)
        self.assertAlmostEqual(mesh.volume / bead.volume, 1.0, delta=1e-3)
        self.assertTrue(mesh.watertight()[0])

    def test_probe_beads(self) -> None:
        model = load_probe(PROBE)
        for bead in model.beads:
            mesh = mesh_bead(bead)
            self.assertLess(abs(mesh.volume - bead.volume) / bead.volume, 0.01)
            self.assertTrue(mesh.watertight()[0], bead.bead_id)
        stats = mesh_stats(model)
        self.assertEqual(stats["watertight_fraction"], 1.0)
        self.assertLess(
            abs(stats["mesh_volume"] - model.volume) / model.volume, 0.01
        )


if __name__ == "__main__":
    unittest.main()
