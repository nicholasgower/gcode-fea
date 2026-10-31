from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cross_section import solve_cross_section
from gcode_fea.model import Bead, SliceModel
from gcode_fea.trim import trim_model

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


def make_bead(bead_id: str, role: str, points, width: float, layer: int = 0) -> Bead:
    cross_section = solve_cross_section(
        0.08, 0.2, mode="volume_conserving", nominal_width=width
    )
    return Bead(
        bead_id,
        0,
        layer,
        0.2,
        role,
        tuple(points),
        width,
        0.2,
        0.08,
        cross_section,
    )


@unittest.skipIf(
    importlib.util.find_spec("shapely") is None, "shapely is not installed"
)
class TrimTests(unittest.TestCase):
    def test_infill_is_clipped_at_wall(self) -> None:
        wall = make_bead(
            "wall",
            "Outer wall",
            [(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0), (-5, -5, 0)],
            0.8,
        )
        infill = make_bead(
            "infill", "Sparse infill", [(-7, 0, 0), (7, 0, 0)], 0.4
        )
        model = SliceModel([wall, infill])
        trimmed, report = trim_model(model)
        self.assertEqual(report["trimmed_beads"], 1)
        self.assertEqual(report["dropped_beads"], 0)
        self.assertLess(trimmed.volume, model.volume)
        new_infill = next(b for b in trimmed.beads if b.bead_id.startswith("infill"))
        xs = [point[0] for point in new_infill.points]
        self.assertGreater(min(xs), -5.0)
        self.assertLess(max(xs), 5.0)
        self.assertAlmostEqual(min(xs), -4.6, places=2)
        self.assertAlmostEqual(max(xs), 4.6, places=2)
        self.assertIn("trim", trimmed.meta)

    def test_walls_are_not_trimmed(self) -> None:
        wall = make_bead(
            "wall",
            "Inner wall",
            [(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0), (-5, -5, 0)],
            0.8,
        )
        model = SliceModel([wall])
        trimmed, report = trim_model(model)
        self.assertEqual(trimmed.beads, model.beads)
        self.assertEqual(report["trimmed_beads"], 0)

    def test_no_walls_is_a_noop(self) -> None:
        infill = make_bead(
            "infill", "Sparse infill", [(-7, 0, 0), (7, 0, 0)], 0.4
        )
        model = SliceModel([infill])
        trimmed, report = trim_model(model)
        self.assertEqual(trimmed.beads, model.beads)
        self.assertEqual(report["trimmed_beads"], 0)
        self.assertEqual(report["volume_relative_change"], 0.0)

    def test_infill_fully_inside_wall_is_dropped(self) -> None:
        wall = make_bead(
            "wall",
            "Outer wall",
            [(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0), (-5, -5, 0)],
            2.0,
        )
        infill = make_bead(
            "infill", "Sparse infill", [(4.8, 0, 0), (5.2, 0, 0)], 0.4
        )
        trimmed, report = trim_model(SliceModel([wall, infill]))
        self.assertEqual(report["dropped_beads"], 1)
        self.assertEqual([b.bead_id for b in trimmed.beads], ["wall"])

    def test_phase0_probe(self) -> None:
        from gcode_fea.cache import load_probe

        model = load_probe(PROBE)
        trimmed, report = trim_model(model)
        self.assertGreater(report["wall_beads"], 0)
        self.assertLessEqual(trimmed.volume, model.volume)
        self.assertGreater(len(trimmed.beads), 0)
        self.assertLess(report["volume_relative_change"], 0.5)


if __name__ == "__main__":
    unittest.main()
