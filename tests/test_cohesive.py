from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cohesive import (
    INITIAL_PLA_COHESIVE,
    CohesiveCard,
    CohesiveLaw,
    calibration_plan,
    cohesive_from_bulk,
    mixed_mode_law,
    mixed_mode_strength,
    sensitivity_study,
)
from gcode_fea.materials import PAPER_PLA


class CohesiveLawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.law = CohesiveLaw(strength=40.0, fracture_energy=0.5, stiffness=1.0e5)

    def test_characteristic_separations(self) -> None:
        self.assertAlmostEqual(self.law.critical_separation, 4.0e-4)
        self.assertAlmostEqual(self.law.failure_separation, 0.025)
        self.assertLess(self.law.critical_separation, self.law.failure_separation)

    def test_traction_peaks_at_strength(self) -> None:
        delta_0 = self.law.critical_separation
        self.assertAlmostEqual(self.law.traction(delta_0), 40.0, places=9)
        self.assertAlmostEqual(self.law.traction(delta_0 / 2), 20.0, places=9)
        self.assertEqual(self.law.traction(self.law.failure_separation), 0.0)
        self.assertEqual(self.law.traction(1.0), 0.0)

    def test_energy_reaches_fracture_energy(self) -> None:
        self.assertAlmostEqual(
            self.law.energy(self.law.failure_separation), 0.5, places=9
        )
        self.assertAlmostEqual(self.law.energy(1.0), 0.5, places=12)
        self.assertLess(self.law.energy(self.law.critical_separation), 0.5)

    def test_damage_monotonic(self) -> None:
        samples = [
            self.law.damage(fraction * self.law.failure_separation)
            for fraction in (0.0, 0.1, 0.5, 0.9, 1.0, 2.0)
        ]
        self.assertEqual(samples[0], 0.0)
        self.assertEqual(samples[-1], 1.0)
        self.assertEqual(samples, sorted(samples))
        for value in samples:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_inconsistent_law(self) -> None:
        with self.assertRaises(ValueError):
            CohesiveLaw(strength=40.0, fracture_energy=0.001, stiffness=10.0)


class CohesiveCardTests(unittest.TestCase):
    def test_initial_card_is_flagged_uncalibrated(self) -> None:
        card = INITIAL_PLA_COHESIVE
        self.assertFalse(card.calibrated)
        self.assertIn("estimate", card.source)
        self.assertAlmostEqual(card.sigma_n, 45.38)
        self.assertGreater(card.law("I").failure_separation, 0.0)
        self.assertGreater(card.law("II").failure_separation, 0.0)

    def test_scaled_card_preserves_failure_separation(self) -> None:
        card = INITIAL_PLA_COHESIVE
        scaled = card.scaled(strength=0.5, toughness=0.5)
        self.assertAlmostEqual(scaled.sigma_n, card.sigma_n * 0.5)
        self.assertAlmostEqual(scaled.G_Ic, card.G_Ic * 0.5)
        self.assertAlmostEqual(
            scaled.law("I").critical_separation,
            card.law("I").critical_separation * 0.5,
            places=12,
        )
        # delta_f = 2G / sigma: scaling both leaves the failure separation.
        self.assertAlmostEqual(
            scaled.law("I").failure_separation,
            card.law("I").failure_separation,
            places=12,
        )

    def test_invalid_card(self) -> None:
        with self.assertRaises(ValueError):
            CohesiveCard(
                name="bad",
                sigma_n=0.0,
                sigma_s=10.0,
                G_Ic=0.5,
                G_IIc=1.0,
                stiffness_n=1e5,
                stiffness_s=1e5,
            )

    def test_from_bulk(self) -> None:
        card = cohesive_from_bulk(PAPER_PLA, strength_fraction=0.5)
        self.assertAlmostEqual(card.sigma_n, PAPER_PLA.tensile_strength_mpa * 0.5)
        self.assertFalse(card.calibrated)

    def test_mixed_mode(self) -> None:
        card = INITIAL_PLA_COHESIVE
        self.assertAlmostEqual(mixed_mode_strength(card, 0.0), card.sigma_n)
        self.assertAlmostEqual(mixed_mode_strength(card, 90.0), card.sigma_s)
        intermediate = mixed_mode_strength(card, 45.0)
        self.assertLess(intermediate, card.sigma_n)
        self.assertGreater(intermediate, card.sigma_s / 2.0)
        law = mixed_mode_law(card, 90.0)
        self.assertAlmostEqual(law.fracture_energy, card.G_IIc)
        self.assertAlmostEqual(mixed_mode_law(card, 0.0).fracture_energy, card.G_Ic)

    def test_bad_mode(self) -> None:
        with self.assertRaises(ValueError):
            INITIAL_PLA_COHESIVE.law("III")


class SensitivityTests(unittest.TestCase):
    def test_matrix(self) -> None:
        rows = sensitivity_study()
        self.assertEqual(len(rows), 4 * 3 * 2)
        for row in rows:
            self.assertGreater(row["peak_traction"], 0.0)
            self.assertGreater(row["fracture_energy"], 0.0)
        first = [row for row in rows if row["mode"] == "I"]
        peaks = {
            (row["strength_factor"], round(row["peak_traction"], 6)) for row in first
        }
        self.assertEqual(len(peaks), 4)

    def test_calibration_plan(self) -> None:
        plan = calibration_plan()
        self.assertGreaterEqual(len(plan["tests"]), 3)
        modes = {test["mode"] for test in plan["tests"]}
        self.assertIn("I", modes)
        self.assertIn("II", modes)


if __name__ == "__main__":
    unittest.main()
