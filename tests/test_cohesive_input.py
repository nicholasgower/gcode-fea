from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.benchmark import CouponSpec, build_coupon
from gcode_fea.cohesive import INITIAL_PLA_COHESIVE
from gcode_fea.interfaces import extract_interfaces
from gcode_fea.solvers.cohesive_input import (
    split_mesh,
    write_abaqus_cohesive,
    write_cohesive_job,
)
from gcode_fea.solvers.coupon import coupon_node_sets
from gcode_fea.voxel import voxelize


class SplitMeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        cls.coupon = build_coupon(90, spec=spec)
        cls.mesh = voxelize(cls.coupon.model, resolution=0.1)
        cls.split = split_mesh(cls.mesh)

    def test_counts(self) -> None:
        summary = self.split.summary()
        self.assertEqual(summary["solid_elements"], self.mesh.n_elements)
        self.assertGreater(summary["cohesive_elements"], 0)
        self.assertGreater(summary["nodes"], self.mesh.n_nodes)
        self.assertEqual(self.split.n_cohesive, len(self.split.cohesive_kind))

    def test_cohesive_node_pairs_are_coincident(self) -> None:
        nodes = self.split.nodes
        for element in self.split.cohesive[:50]:
            lower = nodes[element[:4]]
            upper = nodes[element[4:]]
            np.testing.assert_allclose(lower, upper, atol=1e-12)
            self.assertEqual(len(set(int(node) for node in element)), 8)

    def test_solid_elements_use_distinct_nodes(self) -> None:
        for element in self.split.elements[:50]:
            self.assertEqual(len(set(int(node) for node in element)), 8)

    def test_element_sets(self) -> None:
        sets = self.split.element_sets()
        self.assertEqual(len(sets["EALL"]), self.split.n_elements)
        self.assertEqual(len(sets["COHESIVE"]), self.split.n_cohesive)
        self.assertEqual(
            len(sets["COHESIVE"]) + len(sets["EALL"]),
            self.split.n_elements + self.split.n_cohesive,
        )
        cohesive_sets = [name for name in sets if name.startswith("COH_") and not name.startswith("COH_SCALE")]
        self.assertGreaterEqual(len(cohesive_sets), 2)

    def test_area_total_matches_face_count(self) -> None:
        self.assertAlmostEqual(
            float(self.split.cohesive_area.sum()),
            self.split.n_cohesive * self.mesh.resolution ** 2,
            places=9,
        )


class AbaqusCohesiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        cls.coupon = build_coupon(90, spec=spec)
        cls.mesh = voxelize(cls.coupon.model, resolution=0.1)
        cls.interfaces = extract_interfaces(
            cls.coupon.model, mesh=cls.mesh, resolution=0.1
        )
        cls.split = split_mesh(cls.mesh)

    def test_deck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_abaqus_cohesive(
                self.split,
                INITIAL_PLA_COHESIVE,
                Path(tmp) / "model.inp",
                node_sets=coupon_node_sets(self.split),
                displacement=0.2,
                interfaces=self.interfaces,
            )
            text = path.read_text()
            self.assertIn("*ELEMENT, TYPE=C3D8, ELSET=EALL", text)
            self.assertIn("*ELEMENT, TYPE=COH3D8, ELSET=COHESIVE", text)
            self.assertIn("*COHESIVE SECTION", text)
            self.assertIn("RESPONSE=TRACTION SEPARATION, THICKNESS=SPECIFIED", text)
            self.assertIn("*DAMAGE INITIATION, CRITERION=QUADS", text)
            self.assertIn("*DAMAGE EVOLUTION, TYPE=ENERGY", text)
            self.assertIn("MIXED MODE BEHAVIOR=BK", text)
            self.assertIn("LEFT_EDGE, 1, 1, 0.200000", text)
            self.assertIn("*OUTPUT, FIELD", text)
            self.assertIn("SDEG", text)
            self.assertEqual(text.count("*COHESIVE SECTION"), text.count("*MATERIAL, NAME=CZM"))
            # One more *S/E line pair per scaled material.
            self.assertGreaterEqual(text.count("*ELASTIC, TYPE=TRACTION"), 1)

    def test_area_scaling_changes_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_abaqus_cohesive(
                self.split,
                INITIAL_PLA_COHESIVE,
                Path(tmp) / "model.inp",
                node_sets=coupon_node_sets(self.split),
                interfaces=self.interfaces,
            )
            text = path.read_text()
            scales = {
                item.area_scale for item in self.interfaces
            }
            keys = {f"CZM_{int(round(scale * 10000)):05d}" for scale in scales}
            for key in keys:
                self.assertIn(f"*MATERIAL, NAME={key}", text)

    def test_cohesive_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = write_cohesive_job(
                self.coupon,
                self.mesh,
                Path(tmp),
                INITIAL_PLA_COHESIVE,
                interfaces=self.interfaces,
            )
            for key in ("model", "interfaces", "cohesive_card", "job"):
                self.assertTrue(Path(job["files"][key]).exists(), key)
            payload = json.loads(Path(job["files"]["job"]).read_text())
            self.assertEqual(payload["schema"], "gcode_fea.cohesive_job.v1")
            self.assertFalse(payload["cohesive"]["calibrated"])
            self.assertGreater(payload["mesh"]["cohesive_elements"], 0)
            interfaces = json.loads(Path(job["files"]["interfaces"]).read_text())
            self.assertEqual(interfaces["schema"], "gcode_fea.interfaces.v1")
            self.assertEqual(
                len(interfaces["interfaces"]), len(self.interfaces)
            )


if __name__ == "__main__":
    unittest.main()
