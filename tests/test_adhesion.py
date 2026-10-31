from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.adhesion import (
    AdhesionModel,
    apply_bond_quality,
    bond_quality,
    quality_scaled_card,
    uncertainty_report,
)
from gcode_fea.benchmark import CouponSpec, build_coupon
from gcode_fea.cohesive import INITIAL_PLA_COHESIVE
from gcode_fea.interfaces import extract_interfaces
from gcode_fea.process import BeadProcess


def process(
    bead_id: str = "a",
    layer: int = 0,
    temp: float = 210.0,
    fan: float = 0.0,
    layer_time: float = 10.0,
    speed: float = 50.0,
) -> BeadProcess:
    return BeadProcess(
        bead_id=bead_id,
        layer_index=layer,
        speed_mm_s=speed,
        nozzle_temp_c=temp,
        fan_percent=fan,
        layer_time_s=layer_time,
        matched_segments=1,
        source="gcode",
    )


class AdhesionModelTests(unittest.TestCase):
    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            AdhesionModel(cooling_time_s=0.0)
        with self.assertRaises(ValueError):
            AdhesionModel(reference_time_s=0.0)
        with self.assertRaises(ValueError):
            AdhesionModel(ambient_temp_c=250.0)
        with self.assertRaises(ValueError):
            AdhesionModel(min_quality=0.5, max_quality=0.2)

    def test_rate_at_reference_is_one(self) -> None:
        model = AdhesionModel()
        self.assertAlmostEqual(model.rate(model.reference_temp_c), 1.0, places=12)
        self.assertGreater(model.rate(model.reference_temp_c + 30), 1.0)
        self.assertLess(model.rate(model.reference_temp_c - 30), 1.0)

    def test_cooling(self) -> None:
        model = AdhesionModel()
        hot = model.cooled_temperature(210.0, 0.0, 0.0)
        warm = model.cooled_temperature(210.0, 0.0, 1.0)
        cold = model.cooled_temperature(210.0, 0.0, 100.0)
        self.assertAlmostEqual(hot, 210.0)
        self.assertGreater(warm, cold)
        self.assertGreaterEqual(cold, model.ambient_temp_c - 1e-9)
        # Fan cools faster.
        self.assertLess(
            model.cooled_temperature(210.0, 100.0, 1.0), warm
        )

    def test_quality_bounds(self) -> None:
        model = AdhesionModel()
        self.assertEqual(model.quality(210.0, 0.0), model.min_quality)
        self.assertLessEqual(model.quality(250.0, 1e6), model.max_quality)
        self.assertGreaterEqual(model.quality(30.0, 1e-6), model.min_quality)


class BondQualityTests(unittest.TestCase):
    def test_hotter_is_better(self) -> None:
        hot = bond_quality(process(temp=230), process(temp=230), "interlayer")
        cold = bond_quality(process(temp=190), process(temp=190), "interlayer")
        self.assertGreater(hot.quality, cold.quality)

    def test_more_fan_is_worse(self) -> None:
        low = bond_quality(process(fan=0), process(fan=0), "interlayer")
        high = bond_quality(process(fan=100), process(fan=100), "interlayer")
        self.assertLess(high.quality, low.quality)

    def test_longer_layer_time_is_worse_for_interlayer(self) -> None:
        short = bond_quality(process(layer_time=5), process(layer_time=5), "interlayer")
        long = bond_quality(process(layer_time=40), process(layer_time=40), "interlayer")
        self.assertLess(long.quality, short.quality)

    def test_intralayer_wait_is_short(self) -> None:
        detail = bond_quality(process(), process(), "intralayer", distance_mm=0.4)
        self.assertLess(detail.wait_s, 0.05)
        self.assertGreater(detail.quality, 0.9)

    def test_bad_kind(self) -> None:
        with self.assertRaises(ValueError):
            bond_quality(process(), process(), "glue")


class ApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        cls.coupon = build_coupon(90, spec=spec)
        cls.interfaces = extract_interfaces(cls.coupon.model, resolution=0.2)
        cls.records = {
            bead.bead_id: process(
                bead_id=bead.bead_id,
                layer=bead.layer_index,
                temp=210.0,
                fan=60.0,
                layer_time=12.0,
            )
            for bead in cls.coupon.model.beads
        }

    def test_apply(self) -> None:
        interfaces = copy.deepcopy(self.interfaces)
        scored, summary = apply_bond_quality(interfaces, self.records)
        self.assertEqual(summary["scored"], len(interfaces))
        self.assertEqual(summary["missing_process"], 0)
        for item in scored:
            self.assertGreater(item.bond_quality, 0.0)
            self.assertLessEqual(item.bond_quality, 1.0)
        self.assertGreater(summary["intralayer_quality"]["mean"], 0.0)
        self.assertGreater(summary["interlayer_quality"]["mean"], 0.0)
        self.assertIn("not calibrated", " ".join(summary["warnings"]))

    def test_missing_process_is_reported(self) -> None:
        records = dict(self.records)
        records.pop(next(iter(records)))
        scored, summary = apply_bond_quality(self.interfaces, records)
        self.assertGreater(summary["missing_process"], 0)
        self.assertTrue(any("missing process" in warning for warning in summary["warnings"]))

    def test_scaled_card(self) -> None:
        card = quality_scaled_card(INITIAL_PLA_COHESIVE, 0.5)
        self.assertAlmostEqual(card.sigma_n, INITIAL_PLA_COHESIVE.sigma_n * 0.5)
        self.assertAlmostEqual(card.G_Ic, INITIAL_PLA_COHESIVE.G_Ic * 0.5)
        self.assertFalse(card.calibrated)
        self.assertIn("bond quality", card.source)

    def test_bad_quality(self) -> None:
        with self.assertRaises(ValueError):
            quality_scaled_card(INITIAL_PLA_COHESIVE, 0.0)


class UncertaintyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        cls.coupon = build_coupon(90, spec=spec)
        cls.interfaces = extract_interfaces(cls.coupon.model, resolution=0.2)
        cls.records = {
            bead.bead_id: process(
                bead_id=bead.bead_id,
                layer=bead.layer_index,
                fan=50.0,
            )
            for bead in cls.coupon.model.beads
        }

    def test_report(self) -> None:
        report = uncertainty_report(
            self.interfaces, self.records, samples=25, seed=3
        )
        self.assertEqual(report["samples"], 25)
        self.assertLessEqual(
            report["mean_quality"]["p05"], report["mean_quality"]["median"]
        )
        self.assertLessEqual(
            report["mean_quality"]["median"], report["mean_quality"]["p95"]
        )
        self.assertGreater(report["strength_factor"]["median"], 0.0)
        self.assertIn("calibration_domain", report)
        self.assertTrue(report["warnings"])

    def test_deterministic(self) -> None:
        first = uncertainty_report(
            self.interfaces, self.records, samples=10, seed=7
        )
        second = uncertainty_report(
            self.interfaces, self.records, samples=10, seed=7
        )
        self.assertEqual(first["mean_quality"], second["mean_quality"])
        third = uncertainty_report(
            self.interfaces, self.records, samples=10, seed=8
        )
        self.assertNotEqual(first["mean_quality"], third["mean_quality"])

    def test_bad_samples(self) -> None:
        with self.assertRaises(ValueError):
            uncertainty_report(self.interfaces, self.records, samples=1)


if __name__ == "__main__":
    unittest.main()
