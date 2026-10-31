"""If a body edge enters another body, the merged export must be one body.

Each case builds bead solids at the same layer (or overlapping Z ranges), checks
that a sampled corner edge of one bead is strictly inside another bead, then
exports with the merged engine and asserts a single STEP solid.
"""

from __future__ import annotations

import importlib.util
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cross_section import solve_cross_section
from gcode_fea.merged_step import write_step_merged
from gcode_fea.model import Bead, SliceModel
from gcode_fea.step_planar import build_solids

HAVE_MANIFOLD = importlib.util.find_spec("manifold3d") is not None
MARGIN = 1e-4


def make_bead(
    bead_id: str,
    points,
    width: float = 0.45,
    height: float = 0.2,
    z: float = 0.0,
    layer: int = 0,
) -> Bead:
    cross_section = solve_cross_section(
        0.08, height, mode="volume_conserving", nominal_width=width
    )
    return Bead(
        bead_id=bead_id,
        object_id=0,
        layer_index=layer,
        print_z=z + height,
        role="Sparse infill",
        points=tuple((float(x), float(y), float(z)) for x, y in points),
        nominal_width=width,
        height=height,
        area_per_length=0.08,
        cross_section=cross_section,
    )


def _segments(points):
    result = list(zip(points, points[1:]))
    if len(points) >= 4 and math.dist(points[0], points[-1]) <= 1e-9:
        result = list(zip(points[:-1], points[1:]))
        if len(points) - 1 >= 3:
            result.append((points[-2], points[0]))
    return result


def _inside_polygon(profile, x: float, z: float) -> bool:
    inside = False
    count = len(profile)
    for index in range(count):
        x1, z1 = profile[index]
        x2, z2 = profile[(index + 1) % count]
        if (z1 > z) != (z2 > z):
            x_cross = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
            if x < x_cross:
                inside = not inside
    return inside


def _distance_to_polygon(profile, x: float, z: float) -> float:
    best = math.inf
    count = len(profile)
    for index in range(count):
        ax, az = profile[index]
        bx, bz = profile[(index + 1) % count]
        dx, dz = bx - ax, bz - az
        length_sq = dx * dx + dz * dz
        if length_sq <= 1e-18:
            best = min(best, math.hypot(x - ax, z - az))
            continue
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / length_sq))
        best = min(best, math.hypot(x - (ax + t * dx), z - (az + t * dz)))
    return best


def point_inside_bead(point, bead: Bead, margin: float = MARGIN) -> bool:
    """True when a point is strictly inside the bead's segment-prism compound."""

    z0 = min(p[2] for p in bead.points)
    zrel = point[2] - z0
    if not (MARGIN < zrel < bead.height - MARGIN):
        return False
    profile = bead.cross_section.profile
    for start, end in _segments(bead.points):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        tx, ty = dx / length, dy / length
        nx, ny = -ty, tx
        along = (point[0] - start[0]) * tx + (point[1] - start[1]) * ty
        if along < 0.0 or along > length:
            continue
        lateral = (point[0] - start[0]) * nx + (point[1] - start[1]) * ny
        if _inside_polygon(profile, lateral, zrel):
            if _distance_to_polygon(profile, lateral, zrel) > margin:
                return True
    return False


def edge_samples(bead: Bead):
    """Sample points along the edges of the bead's stitched solids."""

    seen = set()
    for solid in build_solids(bead, profile_stride=4, tolerance=0.0):
        for face in solid.faces:
            for index in range(len(face)):
                u = face[index]
                v = face[(index + 1) % len(face)]
                key = (u, v) if u < v else (v, u)
                if key in seen:
                    continue
                seen.add(key)
                a, b = solid.points[u], solid.points[v]
                for t in (0.2, 0.4, 0.5, 0.6, 0.8):
                    yield (
                        a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                        a[2] + (b[2] - a[2]) * t,
                    )


def edge_enters(a: Bead, b: Bead) -> bool:
    return any(point_inside_bead(point, b) for point in edge_samples(a))


def was_merged(a: Bead, b: Bead) -> bool:
    return edge_enters(a, b) or edge_enters(b, a)


def crossing(angle_deg: float, offset: float = 0.0, length: float = 5.0):
    angle = math.radians(angle_deg)
    dx, dy = math.cos(angle) * length, math.sin(angle) * length
    nx, ny = -math.sin(angle) * offset, math.cos(angle) * offset
    return [
        (nx - dx, ny - dy),
        (nx + dx, ny + dy),
    ]


def case_models():
    cases: list[tuple[str, list[Bead]]] = [
        (
            "perpendicular_centers",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("y", [(0, -5), (0, 5)]),
            ],
        ),
        (
            "perpendicular_offset",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("y", [(0.15, -5), (0.15, 5)]),
            ],
        ),
        (
            "perpendicular_near_edge",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("y", [(0.21, -5), (0.21, 5)]),
            ],
        ),
        (
            "angled_30",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("diag", crossing(30)),
            ],
        ),
        (
            "angled_45",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("diag", crossing(45)),
            ],
        ),
        (
            "angled_60",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("diag", crossing(60)),
            ],
        ),
        (
            "t_junction_end_inside",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("stub", [(0, 0), (0, 5)]),
            ],
        ),
        (
            "corner_end_inside",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("stub", [(0.1, 0.1), (0.1, 5)]),
            ],
        ),
        (
            "l_bend_crossed",
            [
                make_bead("bend", [(-5, 0), (0, 0), (0, 5)]),
                make_bead("y", [(0, -5), (0, 5)]),
            ],
        ),
        (
            "three_crossings",
            [
                make_bead("x", [(-5, 0), (5, 0)]),
                make_bead("y", [(0, -5), (0, 5)]),
                make_bead("diag", crossing(45)),
            ],
        ),
        (
            "different_sizes",
            [
                make_bead("wide", [(-5, 0), (5, 0)], width=0.8, height=0.3),
                make_bead("narrow", [(0, -5), (0, 5)], width=0.4, height=0.2),
            ],
        ),
        (
            "overlapping_z",
            [
                make_bead("low", [(-5, 0), (5, 0)], z=0.0),
                make_bead("high", [(0, -5), (0, 5)], z=0.1),
            ],
        ),
    ]
    return cases


@unittest.skipUnless(HAVE_MANIFOLD, "manifold3d is not installed")
class EdgeInsideBodyMergeTests(unittest.TestCase):
    def assert_one_body(self, beads: list[Bead], label: str) -> None:
        model = SliceModel(beads)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / f"{label}.step"
            result = write_step_merged(
                model,
                target,
                tolerance=0.005,
                max_triangles=120_000,
            )
            text = target.read_text()
        self.assertEqual(result["solids"], 1, label)
        self.assertEqual(
            len(re.findall(r"= MANIFOLD_SOLID_BREP\(", text)),
            1,
            label,
        )
        self.assertGreater(result["triangles"], 0, label)

    def test_cases(self) -> None:
        for label, beads in case_models():
            with self.subTest(case=label):
                self.assertTrue(
                    was_merged(beads[0], beads[1])
                    or any(
                        was_merged(a, b)
                        for index, a in enumerate(beads)
                        for b in beads[index + 1 :]
                    ),
                    f"premise failed: no corner edge of one body enters another ({label})",
                )
                self.assert_one_body(beads, label)

    def test_separated_bodies_are_not_forced_together(self) -> None:
        low = make_bead("low", [(-5, 0), (5, 0)], z=0.0)
        high = make_bead("high", [(-5, 2), (5, 2)], z=0.5)
        self.assertFalse(was_merged(low, high))
        model = SliceModel([low, high])
        with tempfile.TemporaryDirectory() as tmp:
            result = write_step_merged(
                model,
                Path(tmp) / "separated.step",
                tolerance=0.005,
            )
        self.assertEqual(result["solids"], 2)


if __name__ == "__main__":
    unittest.main()
