from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.benchmark import (
    ANGLES,
    CouponSpec,
    PAPER_STRENGTH_SIMULATION_RANKINE,
    PAPER_STRENGTH_SIMULATION_VON_MISES,
    build_coupon,
    compare_results,
    coupon_metrics,
)
from gcode_fea.materials import PAPER_PLA, octagon_area, paper_area_scale

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "benchmark"
    / "coupons.json"
)


class BenchmarkGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.coupons = {angle: build_coupon(angle) for angle in ANGLES}

    def test_all_angles_build(self) -> None:
        for angle in ANGLES:
            coupon = self.coupons[angle]
            self.assertGreater(len(coupon.model.beads), 0)
            self.assertEqual(coupon.meta["angle_deg"], float(angle))
            self.assertEqual(len(coupon.model.layers()), 1)
            for bead in coupon.model.beads:
                self.assertEqual(bead.role, "strand")
                self.assertEqual(bead.cross_section.mode, "paper_octagon")
                self.assertAlmostEqual(
                    bead.cross_section.area,
                    octagon_area(0.4, 0.2, 0.7, 0.2),
                    places=12,
                )

    def test_orientation(self) -> None:
        zero = self.coupons[0]
        for bead in zero.model.beads:
            ys = {round(point[1], 9) for point in bead.points}
            self.assertEqual(len(ys), 1)
        ninety = self.coupons[90]
        for bead in ninety.model.beads:
            xs = {round(point[0], 9) for point in bead.points}
            self.assertEqual(len(xs), 1)

    def test_strand_count_scales_with_perpendicular_extent(self) -> None:
        counts = {angle: len(self.coupons[angle].model.beads) for angle in ANGLES}
        self.assertLess(counts[0], counts[30])
        self.assertLess(counts[30], counts[90])
        # 0 deg: 10 mm wide / 0.4 mm spacing; 90 deg: 50 mm long / 0.4 mm spacing.
        self.assertEqual(counts[0], 26)
        self.assertEqual(counts[90], 126)

    def test_volume_and_void_fraction(self) -> None:
        for angle in ANGLES:
            metrics = coupon_metrics(self.coupons[angle], resolution=0.2)
            self.assertAlmostEqual(
                metrics["volume"],
                len(self.coupons[angle].model.beads)
                * self._mean_length(self.coupons[angle])
                * octagon_area(0.4, 0.2, 0.7, 0.2),
                places=6,
            )
            self.assertGreater(metrics["void_fraction"], 0.05)
            self.assertLess(metrics["void_fraction"], 0.2)

    def test_boundary_conditions(self) -> None:
        bc = self.coupons[45].boundary_conditions
        self.assertEqual(bc["load_axis"], "x")
        self.assertAlmostEqual(bc["gauge_length"], 50.0)
        self.assertIn("left_center", bc["fixed"]["x"])
        self.assertEqual(bc["displacement"]["dof"], "x")

    def test_layers(self) -> None:
        spec = CouponSpec(angle_deg=45, layers=3, length=4.0, width=2.0)
        coupon = build_coupon(45, spec=spec)
        self.assertEqual(len(coupon.model.layers()), 3)
        for bead in coupon.model.beads:
            self.assertAlmostEqual(
                bead.bottom_z, bead.layer_index * 0.2, delta=1e-9
            )

    @staticmethod
    def _mean_length(coupon) -> float:
        lengths = [bead.length for bead in coupon.model.beads]
        return sum(lengths) / len(lengths)


class MaterialTests(unittest.TestCase):
    def test_paper_scaling(self) -> None:
        self.assertAlmostEqual(paper_area_scale(0.7, 0.2), 1.136363636, places=6)
        self.assertAlmostEqual(paper_area_scale(0.7, 0.225), 1.131542, places=6)
        self.assertAlmostEqual(octagon_area(0.4, 0.2, 0.7, 0.2), 0.0704, places=12)
        self.assertAlmostEqual(PAPER_PLA.youngs_modulus_mpa, 3901.0)
        self.assertAlmostEqual(PAPER_PLA.tensile_strength_mpa, 45.38)

    def test_bad_fraction(self) -> None:
        with self.assertRaises(ValueError):
            paper_area_scale(1.2, 0.2)


class FixtureTests(unittest.TestCase):
    def test_checked_in_coupons(self) -> None:
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(fixture["schema"], "gcode_fea.benchmark_fixture.v1")
        for angle_text, expected in fixture["coupons"].items():
            angle = int(angle_text)
            metrics = coupon_metrics(build_coupon(angle), resolution=0.2)
            self.assertEqual(metrics["strand_count"], expected["strand_count"])
            self.assertAlmostEqual(
                metrics["volume"], expected["volume"], places=9
            )
            self.assertAlmostEqual(
                metrics["void_fraction"], expected["void_fraction"], places=9
            )
            self.assertAlmostEqual(
                metrics["strand_area"], expected["strand_area"], places=12
            )

    def test_geometry_matches_fixture_bbox(self) -> None:
        fixture = json.loads(FIXTURE.read_text())
        for angle_text, expected in fixture["coupons"].items():
            bbox = build_coupon(int(angle_text)).model.bbox()
            for actual, reference in zip(bbox, expected["bbox"]):
                self.assertAlmostEqual(actual, reference, places=6)


class ComparisonTests(unittest.TestCase):
    def test_exact_reference(self) -> None:
        results = {
            angle: {"sigma_f": value}
            for angle, value in PAPER_STRENGTH_SIMULATION_RANKINE.items()
        }
        comparison = compare_results(results, criterion="rankine")
        for row in comparison["rows"]:
            self.assertAlmostEqual(row["sigma_f_relative_error"], 0.0, places=12)
        self.assertTrue(comparison["strength_decreases_with_angle"])
        self.assertTrue(comparison["zero_degree_strongest"])

    def test_non_monotonic_von_mises_is_flagged(self) -> None:
        results = {
            angle: {"sigma_f": value}
            for angle, value in PAPER_STRENGTH_SIMULATION_VON_MISES.items()
        }
        comparison = compare_results(results, criterion="von_mises")
        self.assertFalse(comparison["strength_decreases_with_angle"])

    def test_unknown_criterion(self) -> None:
        with self.assertRaises(ValueError):
            compare_results({0: {"E": 1.0}}, criterion="nope")


if __name__ == "__main__":
    unittest.main()
