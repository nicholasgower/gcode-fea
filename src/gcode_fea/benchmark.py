"""Paper benchmark coupons, boundary conditions, and comparisons.

The benchmark reproduces the unidirectional PLA tensile cases from Seibel &
Kiendl (2025): 0/30/45/60/90 degrees between the strand orientation and the
load axis. The paper simulates one layer of a 10 x 50 mm gauge section with
0.4 mm wide, 0.2 mm high strands.

This module builds the coupon geometry analytically (no slicer needed), emits
the boundary/load recipe, and compares solver results with the published
values. Running a real solver is optional and external; see
``docs/paper_benchmark.md``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .contacts import contact_stats, detect_contacts
from .cross_section import solve_cross_section
from .materials import PAPER_PLA, Material, octagon_area
from .model import Bead, SliceModel

ANGLES = (0, 30, 45, 60, 90)

#: Published values from the paper's Table 2 (MPa).
PAPER_E_EXPERIMENT = {0: 3434.0, 30: 2762.0, 45: 2627.0, 60: 2334.0, 90: 2337.0}
PAPER_E_SIMULATION = {0: 3439.0, 30: 2962.0, 45: 2667.0, 60: 2488.0, 90: 2385.0}
PAPER_STRENGTH_EXPERIMENT = {
    0: 39.95,
    30: 27.47,
    45: 20.16,
    60: 14.17,
    90: 7.24,
}
PAPER_STRENGTH_SIMULATION_RANKINE = {
    0: 39.88,
    30: 10.65,
    45: 7.68,
    60: 7.46,
    90: 7.39,
}
PAPER_STRENGTH_SIMULATION_VON_MISES = {
    0: 39.88,
    30: 10.65,
    45: 8.63,
    60: 8.65,
    90: 10.72,
}


@dataclass(frozen=True)
class CouponSpec:
    """Geometry and process inputs for one benchmark coupon."""

    angle_deg: float
    length: float = 50.0
    width: float = 10.0
    bead_width: float = 0.4
    bead_height: float = 0.2
    c_h: float = 0.7
    c_v: float = 0.2
    layers: int = 1
    strand_spacing: float | None = None
    edge_margin: float = 0.0

    @property
    def spacing(self) -> float:
        return self.bead_width if self.strand_spacing is None else self.strand_spacing

    @property
    def strand_area(self) -> float:
        return octagon_area(self.bead_width, self.bead_height, self.c_h, self.c_v)

    @property
    def layer_height(self) -> float:
        return self.bead_height

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["spacing"] = self.spacing
        payload["strand_area"] = self.strand_area
        return payload


@dataclass
class Coupon:
    spec: CouponSpec
    model: SliceModel
    boundary_conditions: dict
    meta: dict = field(default_factory=dict)

    @property
    def angle_deg(self) -> float:
        return self.spec.angle_deg

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.to_dict(),
            "boundary_conditions": self.boundary_conditions,
            "meta": self.meta,
        }


def build_coupon(
    angle_deg: float,
    spec: CouponSpec | None = None,
    material: Material = PAPER_PLA,
) -> Coupon:
    """Build one unidirectional coupon as a bead model.

    Strands run at ``angle_deg`` to the +x load axis. Within a layer the
    raster alternates direction, which does not change the orientation. With
    ``layers > 1`` each layer uses the same orientation, matching the paper's
    unidirectional layup.
    """

    if spec is None:
        spec = CouponSpec(angle_deg=angle_deg)
    elif spec.angle_deg != angle_deg:
        spec = CouponSpec(**{**asdict(spec), "angle_deg": angle_deg})
    if spec.layers < 1:
        raise ValueError("layers must be at least 1")
    if spec.length <= 0 or spec.width <= 0:
        raise ValueError("coupon length and width must be positive")
    if spec.spacing <= 0:
        raise ValueError("strand spacing must be positive")

    theta = math.radians(angle_deg)
    direction = (math.cos(theta), math.sin(theta))
    normal = (-math.sin(theta), math.cos(theta))
    half_length = spec.length / 2.0
    half_width = spec.width / 2.0
    # Extent of the coupon measured perpendicular to the strands.
    extent = abs(spec.length * normal[0]) + abs(spec.width * normal[1])
    extent = max(0.0, extent - 2.0 * spec.edge_margin)
    count = max(1, int(math.ceil(extent / spec.spacing)) + 1)
    start = -(count - 1) * spec.spacing / 2.0

    area = spec.strand_area
    beads: list[Bead] = []
    for layer in range(spec.layers):
        z = layer * spec.bead_height
        print_z = (layer + 1) * spec.bead_height
        for index in range(count):
            offset = start + index * spec.spacing
            segment = _clip_line(
                offset,
                direction,
                normal,
                half_length,
                half_width,
                spec.edge_margin,
            )
            if segment is None:
                continue
            # Alternate the raster direction for a deterministic print order.
            if (index + layer) % 2 == 1:
                segment = (segment[1], segment[0])
            points = (
                (segment[0][0], segment[0][1], z),
                (segment[1][0], segment[1][1], z),
            )
            cross_section = solve_cross_section(
                area=area,
                height=spec.bead_height,
                mode="paper_octagon",
                nominal_width=spec.bead_width,
                c_h=spec.c_h,
                c_v=spec.c_v,
            )
            beads.append(
                Bead(
                    bead_id=(
                        f"coupon{int(round(angle_deg)):03d}_"
                        f"L{layer:04d}_P{index:05d}"
                    ),
                    object_id=int(round(angle_deg)),
                    layer_index=layer,
                    print_z=print_z,
                    role="strand",
                    points=points,
                    nominal_width=spec.bead_width,
                    height=spec.bead_height,
                    area_per_length=area,
                    cross_section=cross_section,
                    material=material.name,
                    path_index=index,
                    source="analytic coupon",
                )
            )

    if not beads:
        raise ValueError("coupon generation produced no strands")

    meta = {
        "kind": "paper_benchmark_coupon",
        "angle_deg": float(angle_deg),
        "length": spec.length,
        "width": spec.width,
        "layers": spec.layers,
        "material": material.as_card(),
        "c_h": spec.c_h,
        "c_v": spec.c_v,
        "units": "mm",
        "frame": "printer",
    }
    model = SliceModel(beads=beads, meta=meta)
    return Coupon(
        spec=spec,
        model=model,
        boundary_conditions=boundary_conditions(spec),
        meta=meta,
    )


def boundary_conditions(spec: CouponSpec) -> dict:
    """Boundary and load recipe for the reduced coupon.

    Mapping of the paper's Fig. 8 into the coupon frame (load along +x):

    - x fixed at the centre of the left and right edges (grip centre),
    - y fixed at the four corners,
    - z fixed along the right edge,
    - x displacement applied to the left edge in load steps.
    """

    return {
        "load_axis": "x",
        "gauge_length": spec.length,
        "gauge_width": spec.width,
        "gauge_thickness": spec.bead_height * spec.layers,
        "fixed": {
            "x": ["left_center", "right_center"],
            "y": ["left_corners", "right_corners"],
            "z": ["right_edge"],
        },
        "displacement": {"edge": "left_edge", "dof": "x", "schedule": "incremental"},
        "purpose": (
            "Reproduce the paper's grip conditions without modelling the full "
            "dogbone. Left/right edges are traction free in y and z except at "
            "the listed grip points."
        ),
    }


def coupon_metrics(
    coupon: Coupon,
    resolution: float = 0.1,
) -> dict:
    """Geometry, contact, and section metrics for one coupon."""

    model = coupon.model
    spec = coupon.spec
    contacts = detect_contacts(model, resolution=resolution)
    min_width = spec.bead_width
    stats = contact_stats(contacts, resolution, min_width)
    projected_area = spec.width * spec.length
    return {
        "angle_deg": coupon.angle_deg,
        "strand_count": len(model.beads),
        "layer_count": len(model.layers()),
        "strand_area": spec.strand_area,
        "volume": model.volume,
        "projected_area": projected_area,
        "void_fraction": 1.0 - model.volume / (projected_area * spec.bead_height * spec.layers),
        "contacts": stats,
    }


def compare_results(
    results: dict[int | float, dict],
    criterion: str = "rankine",
) -> dict:
    """Compare simulated ``E`` and ``sigma_f`` with the paper's values.

    ``results`` maps an angle to ``{"E": float, "sigma_f": float}``. Missing
    angles are skipped. The comparison records absolute and relative errors
    plus the qualitative checks the paper uses to judge the model.
    """

    if criterion not in ("rankine", "von_mises"):
        raise ValueError("criterion must be 'rankine' or 'von_mises'")
    reference_strength = (
        PAPER_STRENGTH_SIMULATION_RANKINE
        if criterion == "rankine"
        else PAPER_STRENGTH_SIMULATION_VON_MISES
    )
    rows = []
    for angle in sorted(results):
        key = int(angle)
        row = {"angle_deg": key}
        value = results[angle]
        if "E" in value and key in PAPER_E_SIMULATION:
            row["E"] = value["E"]
            row["E_reference"] = PAPER_E_SIMULATION[key]
            row["E_relative_error"] = _relative_error(
                value["E"], PAPER_E_SIMULATION[key]
            )
        if "sigma_f" in value and key in reference_strength:
            row["sigma_f"] = value["sigma_f"]
            row["sigma_f_reference"] = reference_strength[key]
            row["sigma_f_relative_error"] = _relative_error(
                value["sigma_f"], reference_strength[key]
            )
        rows.append(row)

    strengths = [row["sigma_f"] for row in rows if "sigma_f" in row]
    monotonic = all(
        later <= earlier + 1e-9
        for earlier, later in zip(strengths, strengths[1:])
    )
    zero = next((row for row in rows if row["angle_deg"] == 0), None)
    nonzero = [row for row in rows if row["angle_deg"] != 0 and "sigma_f" in row]
    zero_strongest = bool(zero and nonzero and zero.get("sigma_f", 0.0) > max(
        row["sigma_f"] for row in nonzero
    ))
    return {
        "criterion": criterion,
        "rows": rows,
        "strength_decreases_with_angle": monotonic,
        "zero_degree_strongest": zero_strongest,
        "reference": {
            "E_simulation": PAPER_E_SIMULATION,
            "sigma_f_simulation": reference_strength,
        },
    }


def _clip_line(
    offset: float,
    direction: tuple[float, float],
    normal: tuple[float, float],
    half_length: float,
    half_width: float,
    margin: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Clip the infinite line ``offset * normal + t * direction`` to a box."""

    origin = (normal[0] * offset, normal[1] * offset)
    lo = -math.inf
    hi = math.inf
    limits = (
        (origin[0], direction[0], -half_length + margin, half_length - margin),
        (origin[1], direction[1], -half_width + margin, half_width - margin),
    )
    for base, delta, lower, upper in limits:
        if abs(delta) <= 1e-12:
            if base < lower - 1e-9 or base > upper + 1e-9:
                return None
            continue
        t0 = (lower - base) / delta
        t1 = (upper - base) / delta
        if t0 > t1:
            t0, t1 = t1, t0
        lo = max(lo, t0)
        hi = min(hi, t1)
    if hi - lo <= 1e-9:
        return None
    return (
        (origin[0] + lo * direction[0], origin[1] + lo * direction[1]),
        (origin[0] + hi * direction[0], origin[1] + hi * direction[1]),
    )


def _relative_error(value: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return abs(value - reference) / abs(reference)
