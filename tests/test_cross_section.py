from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cross_section import solve_cross_section


def polygon_area(profile: tuple[tuple[float, float], ...]) -> float:
    return abs(
        sum(
            x1 * z2 - x2 * z1
            for (x1, z1), (x2, z2) in zip(profile, profile[1:] + profile[:1])
        )
    ) / 2.0


class CrossSectionTests(unittest.TestCase):
    area = 0.08279860764741898
    height = 0.2
    width = 0.45691338181495667

    def test_volume_conserving(self) -> None:
        cs = solve_cross_section(
            self.area, self.height, "volume_conserving", self.width
        )
        self.assertAlmostEqual(cs.area, self.area, places=12)
        self.assertGreater(cs.width_total, cs.width_flat)
        self.assertAlmostEqual(polygon_area(cs.profile) / self.area, 1.0, delta=2e-3)
        self.assertFalse(cs.warnings)

    def test_nominal_envelope(self) -> None:
        cs = solve_cross_section(self.area, self.height, "nominal_envelope", self.width)
        self.assertAlmostEqual(cs.width_total, self.width, places=12)
        self.assertAlmostEqual(cs.width_flat, self.width, places=12)
        self.assertLess(cs.sagitta, 0.0)
        self.assertAlmostEqual(polygon_area(cs.profile) / self.area, 1.0, delta=2e-3)
        self.assertTrue(any("waisted" in warning for warning in cs.warnings))

    def test_nominal_envelope_rectangle(self) -> None:
        cs = solve_cross_section(
            self.width * self.height, self.height, "nominal_envelope", self.width
        )
        self.assertAlmostEqual(cs.area, self.width * self.height, places=12)
        self.assertAlmostEqual(cs.width_total, self.width, places=12)
        self.assertAlmostEqual(cs.sagitta, 0.0, places=12)

    def test_paper_octagon(self) -> None:
        cs = solve_cross_section(
            self.area, self.height, "paper_octagon", self.width, c_h=0.7, c_v=0.225
        )
        self.assertEqual(len(cs.profile), 8)
        expected = self.width * self.height - 0.5 * (
            self.width - 0.7 * self.width
        ) * (self.height - 0.225 * self.height)
        self.assertAlmostEqual(cs.area, expected, places=12)
        self.assertAlmostEqual(polygon_area(cs.profile), expected, places=12)
        self.assertTrue(any("material scaling" in warning for warning in cs.warnings))

    def test_underfilled_fallback(self) -> None:
        cs = solve_cross_section(1e-6, self.height, "volume_conserving", self.width)
        self.assertTrue(any("thin rectangle" in warning for warning in cs.warnings))
        self.assertAlmostEqual(cs.width_total, 1e-6 / self.height, places=12)

    def test_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            solve_cross_section(self.area, self.height, "nope", self.width)


if __name__ == "__main__":
    unittest.main()
