from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.benchmark import CouponSpec, build_coupon
from gcode_fea.interfaces import (
    extract_interfaces,
    interface_area_by_pair,
    interface_report,
    interface_sets,
)
from gcode_fea.voxel import voxelize


class InterfaceExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        cls.coupon = build_coupon(90, spec=spec)
        cls.model = cls.coupon.model
        cls.mesh = voxelize(cls.model, resolution=0.1)
        cls.interfaces = extract_interfaces(
            cls.model, mesh=cls.mesh, resolution=0.1
        )

    def test_report(self) -> None:
        report = interface_report(self.interfaces)
        self.assertGreater(report["count"], 0)
        self.assertGreater(report["intralayer_count"], 0)
        self.assertGreater(report["interlayer_count"], 0)
        self.assertGreater(report["total_area"], 0.0)
        self.assertGreater(report["raw_face_area"], report["total_area"])

    def test_ids_and_pairs_are_unique(self) -> None:
        ids = [item.interface_id for item in self.interfaces]
        self.assertEqual(len(ids), len(set(ids)))
        pairs = interface_area_by_pair(self.interfaces)
        self.assertEqual(len(pairs), len(self.interfaces))

    def test_areas_are_scaled_from_contacts(self) -> None:
        intralayer = [
            item for item in self.interfaces if item.kind == "intralayer"
        ]
        interlayer = [
            item for item in self.interfaces if item.kind == "interlayer"
        ]
        self.assertTrue(intralayer)
        self.assertTrue(all(item.area_model == "flat_side_scaled" for item in intralayer))
        self.assertTrue(interlayer)
        self.assertTrue(all(item.area_model == "contact_proxy" for item in interlayer))
        for item in self.interfaces:
            self.assertGreater(item.area, 0.0)
            self.assertGreater(item.raw_area, 0.0)
            self.assertAlmostEqual(item.area / item.raw_area, item.area_scale, places=12)
            self.assertGreaterEqual(item.face_count, 1)

    def test_sets(self) -> None:
        grouped = interface_sets(self.interfaces)
        self.assertEqual(
            sum(len(values) for values in grouped.values()), len(self.interfaces)
        )

    def test_mesh_faces_are_quads(self) -> None:
        for item in self.interfaces:
            for face in item.faces:
                self.assertEqual(len(face), 4)
                self.assertLess(max(face), self.mesh.n_nodes)

    def test_without_mesh_has_no_faces(self) -> None:
        interfaces = extract_interfaces(self.model, resolution=0.2)
        self.assertGreater(len(interfaces), 0)
        self.assertTrue(all(item.face_count == 0 for item in interfaces))
        self.assertTrue(all(item.raw_area == 0.0 for item in interfaces))
        self.assertIn("no mesh faces", " ".join(interface_report(interfaces)["warnings"]))


if __name__ == "__main__":
    unittest.main()
