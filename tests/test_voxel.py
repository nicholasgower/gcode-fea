from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.benchmark import CouponSpec, build_coupon
from gcode_fea.voxel import interface_patches, voxelize


class VoxelMeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = CouponSpec(angle_deg=0, length=4.0, width=2.0)
        cls.coupon = build_coupon(0, spec=spec)
        cls.mesh = voxelize(cls.coupon.model, resolution=0.1)

    def test_summary_and_sets(self) -> None:
        summary = self.mesh.summary()
        self.assertGreater(summary["elements"], 0)
        self.assertEqual(summary["beads"], len(self.coupon.model.beads))
        sets = self.mesh.element_sets()
        self.assertIn("EALL", sets)
        self.assertEqual(len(sets["EALL"]), self.mesh.n_elements)
        self.assertIn("LAYER_0000", sets)
        self.assertIn("ROLE_STRAND", sets)
        bead_sets = [name for name in sets if name.startswith("BEAD_")]
        self.assertEqual(len(bead_sets), len(self.coupon.model.beads))

    def test_hexahedra_are_well_formed(self) -> None:
        elements = self.mesh.elements
        self.assertEqual(elements.shape[1], 8)
        self.assertEqual(len(set(elements.flatten())), self.mesh.n_nodes)
        points = self.mesh.nodes[elements]
        # Element diagonals must have a positive length and no collapse.
        span = points.max(axis=1) - points.min(axis=1)
        self.assertTrue((span > 0).all())

    def test_volume_error_shrinks_with_resolution(self) -> None:
        coarse = voxelize(self.coupon.model, resolution=0.2)
        fine = voxelize(self.coupon.model, resolution=0.05)
        coarse_error = abs(coarse.volume - self.coupon.model.volume) / self.coupon.model.volume
        fine_error = abs(fine.volume - self.coupon.model.volume) / self.coupon.model.volume
        self.assertGreater(coarse_error, fine_error)
        self.assertLess(fine_error, 0.03)

    def test_interfaces(self) -> None:
        patches = interface_patches(self.mesh)
        self.assertGreater(len(patches), 0)
        self.assertTrue(all(patch.kind == "intralayer" for patch in patches))
        self.assertTrue(all(patch.area > 0 for patch in patches))
        self.assertGreaterEqual(len(patches), len(self.coupon.model.beads) - 1)
        for patch in patches:
            self.assertAlmostEqual(
                patch.area, len(patch.faces) * self.mesh.resolution ** 2
            )

    def test_too_coarse(self) -> None:
        with self.assertRaises(ValueError):
            voxelize(self.coupon.model, resolution=0.4)

    def test_stacked_layers_have_interlayer_patches(self) -> None:
        spec = CouponSpec(angle_deg=90, length=4.0, width=2.0, layers=2)
        coupon = build_coupon(90, spec=spec)
        mesh = voxelize(coupon.model, resolution=0.1)
        patches = interface_patches(mesh)
        kinds = {patch.kind for patch in patches}
        self.assertIn("interlayer", kinds)
        self.assertGreater(mesh.summary()["layers"], 1)


if __name__ == "__main__":
    unittest.main()
