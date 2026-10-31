from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.benchmark import CouponSpec, build_coupon
from gcode_fea.materials import PAPER_PLA
from gcode_fea.solvers.coupon import coupon_node_sets, write_coupon_job
from gcode_fea.solvers.inp import write_abaqus, write_calculix
from gcode_fea.voxel import voxelize


class SolverInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=0, length=2.0, width=1.0)
        cls.coupon = build_coupon(0, spec=spec)
        cls.mesh = voxelize(cls.coupon.model, resolution=0.1)

    def test_node_sets(self) -> None:
        sets = coupon_node_sets(self.mesh)
        self.assertEqual(
            set(sets),
            {
                "LEFT_EDGE",
                "RIGHT_EDGE",
                "LEFT_CENTER",
                "RIGHT_CENTER",
                "LEFT_CORNERS",
                "RIGHT_CORNERS",
            },
        )
        for name, indices in sets.items():
            self.assertGreater(len(indices), 0, name)
            self.assertEqual(indices.dtype.kind, "i")
            self.assertGreaterEqual(indices.min(), 1)
            self.assertLessEqual(indices.max(), self.mesh.n_nodes)

    def test_abaqus_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_abaqus(
                self.mesh,
                Path(tmp) / "model.inp",
                PAPER_PLA.as_card(),
                node_sets=coupon_node_sets(self.mesh),
                displacement=0.25,
            )
            text = path.read_text()
            self.assertIn("*ELEMENT, TYPE=C3D8, ELSET=EALL", text)
            self.assertIn("*ELASTIC", text)
            self.assertIn("3901.000000, 0.300000", text)
            self.assertIn("LEFT_EDGE, 1, 1, 0.250000", text)
            self.assertIn("*OUTPUT, FIELD", text)
            self.assertIn("*END STEP", text)
            self.assertEqual(text.count("*ELEMENT, TYPE=C3D8"), 1)

    def test_calculix_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_calculix(
                self.mesh,
                Path(tmp) / "model.inp",
                PAPER_PLA.as_card(),
                node_sets=coupon_node_sets(self.mesh),
            )
            text = path.read_text()
            self.assertIn("*NODE FILE", text)
            self.assertIn("U, RF", text)
            self.assertNotIn("*OUTPUT", text)
            self.assertIn("*SOLID SECTION, ELSET=EALL, MATERIAL=PAPER_PLA", text)

    def test_coupon_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = write_coupon_job(
                self.coupon,
                self.mesh,
                Path(tmp),
                PAPER_PLA,
                solver="calculix",
            )
            model = Path(job["files"]["model"])
            self.assertTrue(model.exists())
            self.assertTrue(Path(job["files"]["deletion_plan"]).exists())
            self.assertTrue(Path(job["files"]["mesh_summary"]).exists())
            payload = json.loads(Path(job["files"]["job"]).read_text())
            self.assertEqual(payload["solver"], "calculix")
            self.assertAlmostEqual(payload["coupon"]["length"], 2.0)
            self.assertIn("LEFT_EDGE", payload["node_sets"])
            self.assertEqual(payload["deletion_fraction"], 1e-4)

    def test_unknown_solver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_coupon_job(
                    self.coupon,
                    self.mesh,
                    Path(tmp),
                    PAPER_PLA,
                    solver="ansys",
                )


if __name__ == "__main__":
    unittest.main()
