"""Regression: two perpendicular overlapping extrusions become one STEP body."""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cross_section import solve_cross_section
from gcode_fea.merged_step import write_step_merged
from gcode_fea.model import Bead, SliceModel

HAVE_MANIFOLD = importlib.util.find_spec("manifold3d") is not None


def _gmsh_available() -> bool:
    try:
        import gmsh  # noqa: F401
    except Exception:
        return False
    return True


HAVE_GMSH = _gmsh_available()


def make_bead(bead_id: str, points, width: float = 0.45) -> Bead:
    cross_section = solve_cross_section(
        0.08, 0.2, mode="volume_conserving", nominal_width=width
    )
    return Bead(
        bead_id=bead_id,
        object_id=0,
        layer_index=0,
        print_z=0.2,
        role="Sparse infill",
        points=tuple(points),
        nominal_width=width,
        height=0.2,
        area_per_length=0.08,
        cross_section=cross_section,
    )


def crossing_model() -> SliceModel:
    """Two 10 mm extrusions on the same layer, crossing at the origin."""

    return SliceModel(
        [
            make_bead("along_x", [(-5.0, 0.0, 0.0), (5.0, 0.0, 0.0)]),
            make_bead("along_y", [(0.0, -5.0, 0.0), (0.0, 5.0, 0.0)]),
        ]
    )


@unittest.skipUnless(HAVE_MANIFOLD, "manifold3d is not installed")
class PerpendicularOverlapTests(unittest.TestCase):
    def test_two_perpendicular_extrusions_export_one_body(self) -> None:
        model = crossing_model()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "crossing.step"
            result = write_step_merged(
                model,
                target,
                tolerance=0.0,
                max_triangles=60_000,
            )
            text = target.read_text()

        # One connected union, not two overlapping prisms.
        self.assertEqual(result["solids"], 1)
        self.assertEqual(
            len(re.findall(r"= MANIFOLD_SOLID_BREP\(", text)), 1
        )
        self.assertEqual(len(re.findall(r"= CLOSED_SHELL\(", text)), 1)
        self.assertNotIn("FACETED_BREP", text)
        self.assertGreater(result["triangles"], 0)

        # Overlap volume is counted once.
        bead_sum = sum(bead.volume for bead in model.beads)
        self.assertLess(result["volume"], bead_sum)
        self.assertGreater(result["volume"], max(bead.volume for bead in model.beads))

    @unittest.skipUnless(HAVE_GMSH, "gmsh is not installed or could not be loaded")
    def test_occt_reads_one_solid_with_the_union_volume(self) -> None:
        import gmsh

        model = crossing_model()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "crossing.step"
            result = write_step_merged(model, target, tolerance=0.0)

            gmsh.initialize()
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("crossing")
            try:
                gmsh.open(str(target))
                solids = gmsh.model.getEntities(3)
                volume = sum(
                    gmsh.model.occ.getMass(dim, tag)
                    for dim, tag in solids
                )
            finally:
                gmsh.finalize()

        self.assertEqual(len(solids), 1)
        self.assertAlmostEqual(volume, result["volume"], places=6)


if __name__ == "__main__":
    unittest.main()
