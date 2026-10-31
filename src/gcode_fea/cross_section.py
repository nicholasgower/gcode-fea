"""Volume-conserving bead cross-section solvers."""

from __future__ import annotations

import math

from .model import CrossSection, Point2

MODES = ("volume_conserving", "nominal_envelope", "paper_octagon")


def solve_cross_section(
    area: float,
    height: float,
    mode: str = "volume_conserving",
    nominal_width: float | None = None,
    c_h: float = 0.7,
    c_v: float = 0.225,
    arc_samples: int = 24,
) -> CrossSection:
    """Solve a bead cross-section for the requested deposited area.

    `area` is the deposited area per unit length (`mm3_per_mm`).
    `height` is the layer height.
    `nominal_width` is the slicer's target line width; when omitted it falls
    back to the rectangular width `area / height`.
    """

    if area <= 0:
        raise ValueError("area must be positive")
    if height <= 0:
        raise ValueError("height must be positive")
    if mode not in MODES:
        raise ValueError(f"unknown cross-section mode: {mode}")
    if nominal_width is None or nominal_width <= 0:
        nominal_width = area / height
    nominal_width = float(nominal_width)
    c_h = min(max(float(c_h), 0.0), 1.0)
    c_v = min(max(float(c_v), 0.0), 1.0)

    if mode == "paper_octagon":
        return _paper_octagon(area, height, nominal_width, c_h, c_v)

    if mode == "nominal_envelope":
        return _nominal_envelope(area, height, nominal_width, arc_samples)

    return _volume_conserving(area, height, nominal_width, c_h, arc_samples)


def _volume_conserving(
    area: float,
    height: float,
    nominal_width: float,
    c_h: float,
    arc_samples: int,
) -> CrossSection:
    w_flat = max(0.0, min(nominal_width, c_h * nominal_width))
    base = w_flat * height
    warnings: list[str] = []

    if abs(area - base) <= 1e-12:
        return _rectangle(area, height, w_flat, "volume_conserving", warnings)

    if area > base:
        def objective(s: float) -> float:
            return w_flat * height + 2.0 * _segment_area(s, height)

        hi = max(height, 1e-6)
        while objective(hi) < area and hi < 1e6:
            hi *= 2.0
        s = _bisect(objective, 0.0, hi, area)
        w_total = w_flat + 2.0 * s
        radius = _arc_radius(s, height)
        profile = _barrel_profile(w_flat, w_total, height, s, arc_samples)
        return CrossSection(
            mode="volume_conserving",
            height=height,
            area=area,
            width_flat=w_flat,
            width_total=w_total,
            flat_side_height=0.0,
            sagitta=s,
            radius=radius,
            profile=profile,
            warnings=tuple(warnings),
        )

    # Under-filled: concave sides if the requested area is still representable.
    d_max = max(0.0, w_flat / 2.0 - 1e-9)
    if d_max > 0:
        def objective(d: float) -> float:
            return w_flat * height - 2.0 * _segment_area(d, height)

        min_area = objective(d_max)
        if area >= min_area:
            d = _bisect(objective, 0.0, d_max, area)
            s = -d
            w_total = w_flat - 2.0 * d
            radius = _arc_radius(d, height)
            profile = _barrel_profile(w_flat, w_total, height, s, arc_samples)
            warnings.append("concave sides: requested area is below the flat rectangle")
            return CrossSection(
                mode="volume_conserving",
                height=height,
                area=area,
                width_flat=w_flat,
                width_total=w_total,
                flat_side_height=0.0,
                sagitta=s,
                radius=radius,
                profile=profile,
                warnings=tuple(warnings),
            )

    warnings.append("requested area is too small for the contact width; using a thin rectangle")
    fallback_width = area / height
    return _rectangle(area, height, fallback_width, "volume_conserving", warnings)


def _nominal_envelope(
    area: float,
    height: float,
    nominal_width: float,
    arc_samples: int,
) -> CrossSection:
    """Keep the nominal flat top/bottom width; make the sides concave if needed.

    The nominal envelope is the slicer's target width. If the deposited area is
    below `width * height`, the bead is waisted: the top/bottom flats stay at
    the nominal width and the side arcs cut inward. If the area is above the
    rectangle, the bead cannot fit inside the nominal width and is widened by
    the volume-conserving solver.
    """

    w = nominal_width
    max_area = w * height

    if area > max_area:
        result = _volume_conserving(area, height, w, 1.0, arc_samples)
        return _replace_with_warning(
            result, "nominal envelope too narrow; bead widened to conserve volume"
        )
    if abs(area - max_area) <= 1e-12:
        return _rectangle(area, height, w, "nominal_envelope", [])

    d_max = max(0.0, w / 2.0 - 1e-9)

    def objective(d: float) -> float:
        return w * height - 2.0 * _segment_area(d, height)

    min_area = objective(d_max) if d_max > 0 else max_area
    if area < min_area:
        warnings = ["nominal envelope too wide for the area; using a thin rectangle"]
        return _rectangle(area, height, area / height, "nominal_envelope", warnings)

    d = _bisect(objective, 0.0, d_max, area)
    sagitta = -d
    w_mid = max(0.0, w - 2.0 * d)
    radius = _arc_radius(d, height)
    profile = _barrel_profile(w, w_mid, height, sagitta, arc_samples)
    return CrossSection(
        mode="nominal_envelope",
        height=height,
        area=area,
        width_flat=w,
        width_total=w,
        flat_side_height=0.0,
        sagitta=sagitta,
        radius=radius,
        profile=profile,
        warnings=("waisted sides: deposited area is below the nominal rectangle",),
    )


def _paper_octagon(
    area: float,
    height: float,
    nominal_width: float,
    c_h: float,
    c_v: float,
) -> CrossSection:
    w = nominal_width
    w_flat = c_h * w
    h_side = c_v * height
    h_side = min(max(h_side, 0.0), height)
    w_flat = min(max(w_flat, 0.0), w)
    geometric_area = w * height - 0.5 * (w - w_flat) * (height - h_side)
    warnings: list[str] = []
    if abs(geometric_area - area) / area > 1e-6:
        warnings.append(
            "paper octagon area differs from deposited area; material scaling is external"
        )
    profile = (
        (-w_flat / 2.0, 0.0),
        (w_flat / 2.0, 0.0),
        (w / 2.0, height / 2.0 - h_side / 2.0),
        (w / 2.0, height / 2.0 + h_side / 2.0),
        (w_flat / 2.0, height),
        (-w_flat / 2.0, height),
        (-w / 2.0, height / 2.0 + h_side / 2.0),
        (-w / 2.0, height / 2.0 - h_side / 2.0),
    )
    return CrossSection(
        mode="paper_octagon",
        height=height,
        area=geometric_area,
        width_flat=w_flat,
        width_total=w,
        flat_side_height=h_side,
        sagitta=0.0,
        radius=None,
        profile=profile,
        warnings=tuple(warnings),
    )


def _rectangle(
    area: float,
    height: float,
    width: float,
    mode: str,
    warnings: list[str],
) -> CrossSection:
    width = max(width, 1e-12)
    profile = (
        (-width / 2.0, 0.0),
        (width / 2.0, 0.0),
        (width / 2.0, height),
        (-width / 2.0, height),
    )
    return CrossSection(
        mode=mode,
        height=height,
        area=area,
        width_flat=width,
        width_total=width,
        flat_side_height=0.0,
        sagitta=0.0,
        radius=None,
        profile=profile,
        warnings=tuple(warnings),
    )


def _replace_with_warning(result: CrossSection, warning: str) -> CrossSection:
    return CrossSection(
        mode=result.mode,
        height=result.height,
        area=result.area,
        width_flat=result.width_flat,
        width_total=result.width_total,
        flat_side_height=result.flat_side_height,
        sagitta=result.sagitta,
        radius=result.radius,
        profile=result.profile,
        warnings=result.warnings + (warning,),
    )


def _segment_area(sagitta: float, height: float) -> float:
    """Area of a circular segment with the given chord height and sagitta."""
    if sagitta <= 0:
        return 0.0
    radius = _arc_radius(sagitta, height)
    alpha = math.acos(max(-1.0, min(1.0, (radius - sagitta) / radius)))
    return radius * radius * alpha - (radius - sagitta) * math.sqrt(
        max(0.0, 2.0 * radius * sagitta - sagitta * sagitta)
    )


def _arc_radius(sagitta: float, height: float) -> float:
    if sagitta <= 0:
        return math.inf
    return sagitta / 2.0 + height * height / (8.0 * sagitta)


def _barrel_profile(
    w_flat: float,
    w_total: float,
    height: float,
    sagitta: float,
    arc_samples: int,
) -> tuple[Point2, ...]:
    """Counter-clockwise barrel profile: flat bottom, right arc, flat top, left arc."""
    if abs(sagitta) <= 1e-12:
        return (
            (-w_flat / 2.0, 0.0),
            (w_flat / 2.0, 0.0),
            (w_flat / 2.0, height),
            (-w_flat / 2.0, height),
        )

    n = max(3, int(arc_samples))
    if sagitta > 0:
        radius = _arc_radius(sagitta, height)
        x_c = w_total / 2.0 - radius
        alpha = math.acos(max(-1.0, min(1.0, (radius - sagitta) / radius)))
        right = []
        for i in range(1, n):
            theta = -alpha + (2.0 * alpha) * i / n
            right.append((x_c + radius * math.cos(theta), height / 2.0 + radius * math.sin(theta)))
    else:
        d = -sagitta
        radius = _arc_radius(d, height)
        x_c = w_total / 2.0 + radius
        alpha = math.acos(max(-1.0, min(1.0, (radius - d) / radius)))
        right = []
        for i in range(1, n):
            theta = (math.pi + alpha) - (2.0 * alpha) * i / n
            right.append((x_c + radius * math.cos(theta), height / 2.0 + radius * math.sin(theta)))

    left = [(-x, z) for x, z in reversed(right)]
    return (
        (-w_flat / 2.0, 0.0),
        (w_flat / 2.0, 0.0),
        *right,
        (w_flat / 2.0, height),
        (-w_flat / 2.0, height),
        *left,
    )


def _bisect(f, lo: float, hi: float, target: float, iterations: int = 100) -> float:
    """Find x in [lo, hi] with f(x) == target for a monotonic f."""
    f_lo = f(lo) - target
    f_hi = f(hi) - target
    if abs(f_lo) <= 1e-14:
        return lo
    if abs(f_hi) <= 1e-14:
        return hi
    if f_lo * f_hi > 0:
        # Return the closest endpoint; the caller should have bracketed.
        return lo if abs(f_lo) < abs(f_hi) else hi
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        f_mid = f(mid) - target
        if abs(f_mid) <= 1e-14:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0
