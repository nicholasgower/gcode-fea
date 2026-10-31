"""Approximate intralayer and interlayer contact detection on a 2.5D grid."""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from .model import Bead, Contact, SliceModel


def detect_contacts(
    model: SliceModel,
    resolution: float = 0.1,
) -> list[Contact]:
    """Detect contacts by rasterizing bead footprints per layer.

    Intralayer contacts are shared footprint boundary length times bead height.
    Interlayer contacts are overlapping footprint area times the flat-top
    contact fraction. The result is a geometric approximation; the FEA recipe
    applies the interface constitutive model separately.
    """

    if resolution <= 0:
        raise ValueError("resolution must be positive")
    if not model.beads:
        return []

    xmin, ymin, _, xmax, ymax, _ = model.bbox()
    margin = max(bead.cross_section.width_total for bead in model.beads) / 2.0 + resolution
    xmin -= margin
    ymin -= margin
    xmax += margin
    ymax += margin
    nx = max(1, int(math.ceil((xmax - xmin) / resolution)) + 1)
    ny = max(1, int(math.ceil((ymax - ymin) / resolution)) + 1)

    layers = model.layers()
    grids: dict[int, tuple[np.ndarray, list[Bead]]] = {}
    for layer_index, beads in layers.items():
        grid = np.full((ny, nx), -1, dtype=np.int32)
        for index, bead in enumerate(beads):
            mask = _rasterize_bead(bead, xmin, ymin, resolution, ny, nx)
            grid[(grid == -1) & mask] = index
        grids[layer_index] = (grid, beads)

    contacts: list[Contact] = []
    for layer_index, (grid, beads) in grids.items():
        contacts.extend(_intralayer_contacts(grid, beads, xmin, ymin, resolution))

    sorted_layers = sorted(grids)
    for lower, upper in zip(sorted_layers, sorted_layers[1:]):
        lower_grid, lower_beads = grids[lower]
        upper_grid, upper_beads = grids[upper]
        contacts.extend(
            _interlayer_contacts(
                lower_grid,
                lower_beads,
                upper_grid,
                upper_beads,
                xmin,
                ymin,
                resolution,
            )
        )

    contacts.sort(key=lambda contact: (contact.kind, contact.id_a, contact.id_b))
    return contacts


def contact_stats(
    contacts: list[Contact],
    resolution: float,
    min_width: float | None = None,
) -> dict:
    intra = [contact for contact in contacts if contact.kind == "intralayer"]
    inter = [contact for contact in contacts if contact.kind == "interlayer"]
    warnings: list[str] = []
    if min_width is not None and resolution > 0.25 * min_width:
        warnings.append("resolution is coarser than one quarter of the narrowest bead")
    return {
        "resolution": resolution,
        "count": len(contacts),
        "intralayer_count": len(intra),
        "interlayer_count": len(inter),
        "intralayer_area": sum(contact.area for contact in intra),
        "interlayer_area": sum(contact.area for contact in inter),
        "warnings": warnings,
    }


def _rasterize_bead(
    bead: Bead,
    xmin: float,
    ymin: float,
    resolution: float,
    ny: int,
    nx: int,
) -> np.ndarray:
    mask = np.zeros((ny, nx), dtype=bool)
    points = list(bead.points)
    if len(points) < 2:
        return mask
    closed = _closed(points)
    if closed:
        points = points[:-1]
    segments = list(zip(points, points[1:]))
    if closed and len(points) >= 3:
        segments.append((points[-1], points[0]))
    half = bead.cross_section.width_total / 2.0
    for start, end in segments:
        _rasterize_segment(mask, start, end, half, xmin, ymin, resolution)
    return mask


def _rasterize_segment(
    mask: np.ndarray,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    half_width: float,
    xmin: float,
    ymin: float,
    resolution: float,
) -> None:
    x0, y0 = start[0], start[1]
    x1, y1 = end[0], end[1]
    ny, nx = mask.shape
    ix0 = max(0, int(math.floor((min(x0, x1) - half_width - xmin) / resolution)))
    ix1 = min(nx - 1, int(math.ceil((max(x0, x1) + half_width - xmin) / resolution)))
    iy0 = max(0, int(math.floor((min(y0, y1) - half_width - ymin) / resolution)))
    iy1 = min(ny - 1, int(math.ceil((max(y0, y1) + half_width - ymin) / resolution)))
    if ix1 < ix0 or iy1 < iy0:
        return
    xs = xmin + (np.arange(ix0, ix1 + 1) + 0.5) * resolution
    ys = ymin + (np.arange(iy0, iy1 + 1) + 0.5) * resolution
    X, Y = np.meshgrid(xs, ys)
    dx = x1 - x0
    dy = y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        d2 = (X - x0) ** 2 + (Y - y0) ** 2
    else:
        t = ((X - x0) * dx + (Y - y0) * dy) / length_sq
        t = np.clip(t, 0.0, 1.0)
        px = x0 + t * dx
        py = y0 + t * dy
        d2 = (X - px) ** 2 + (Y - py) ** 2
    mask[iy0 : iy1 + 1, ix0 : ix1 + 1] |= d2 <= half_width * half_width


def _intralayer_contacts(
    grid: np.ndarray,
    beads: list[Bead],
    xmin: float,
    ymin: float,
    resolution: float,
) -> list[Contact]:
    counts: dict[tuple[int, int], int] = defaultdict(int)
    centroids: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    # Horizontal neighbours (same row, adjacent columns).
    a = grid[:, :-1]
    b = grid[:, 1:]
    valid = (a >= 0) & (b >= 0) & (a != b)
    rows, cols = np.nonzero(valid)
    for row, col in zip(rows, cols):
        ia = int(a[row, col])
        ib = int(b[row, col])
        key = (min(ia, ib), max(ia, ib))
        counts[key] += 1
        centroids[key][0] += xmin + (col + 1.0) * resolution
        centroids[key][1] += ymin + (row + 0.5) * resolution

    # Vertical neighbours (same column, adjacent rows).
    a = grid[:-1, :]
    b = grid[1:, :]
    valid = (a >= 0) & (b >= 0) & (a != b)
    rows, cols = np.nonzero(valid)
    for row, col in zip(rows, cols):
        ia = int(a[row, col])
        ib = int(b[row, col])
        key = (min(ia, ib), max(ia, ib))
        counts[key] += 1
        centroids[key][0] += xmin + (col + 0.5) * resolution
        centroids[key][1] += ymin + (row + 1.0) * resolution

    contacts: list[Contact] = []
    for (ia, ib), count in sorted(counts.items()):
        bead_a = beads[ia]
        bead_b = beads[ib]
        area = count * resolution * ((bead_a.height + bead_b.height) / 2.0)
        center = (centroids[(ia, ib)][0] / count, centroids[(ia, ib)][1] / count)
        ca = _center_xy(bead_a)
        cb = _center_xy(bead_b)
        normal = _normal_2d(cb[0] - ca[0], cb[1] - ca[1])
        z = ((bead_a.print_z - bead_a.height / 2.0) + (bead_b.print_z - bead_b.height / 2.0)) / 2.0
        contacts.append(
            Contact(
                id_a=bead_a.bead_id,
                id_b=bead_b.bead_id,
                kind="intralayer",
                area=area,
                normal=(normal[0], normal[1], 0.0),
                centroid=(center[0], center[1], z),
                overlap=area,
                gap=0.0,
                resolution=resolution,
            )
        )
    return contacts


def _interlayer_contacts(
    lower_grid: np.ndarray,
    lower_beads: list[Bead],
    upper_grid: np.ndarray,
    upper_beads: list[Bead],
    xmin: float,
    ymin: float,
    resolution: float,
) -> list[Contact]:
    valid = (lower_grid >= 0) & (upper_grid >= 0)
    rows, cols = np.nonzero(valid)
    counts: dict[tuple[int, int], int] = defaultdict(int)
    centroids: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row, col in zip(rows, cols):
        ia = int(lower_grid[row, col])
        ib = int(upper_grid[row, col])
        key = (ia, ib)
        counts[key] += 1
        centroids[key][0] += xmin + (col + 0.5) * resolution
        centroids[key][1] += ymin + (row + 0.5) * resolution

    contacts: list[Contact] = []
    for (ia, ib), count in sorted(counts.items()):
        lower = lower_beads[ia]
        upper = upper_beads[ib]
        fraction = min(
            lower.cross_section.flat_top_fraction,
            upper.cross_section.flat_top_fraction,
        )
        area = count * resolution * resolution * fraction
        center = (centroids[(ia, ib)][0] / count, centroids[(ia, ib)][1] / count)
        z = (lower.top_z + upper.bottom_z) / 2.0
        contacts.append(
            Contact(
                id_a=lower.bead_id,
                id_b=upper.bead_id,
                kind="interlayer",
                area=area,
                normal=(0.0, 0.0, 1.0),
                centroid=(center[0], center[1], z),
                overlap=area,
                gap=0.0,
                resolution=resolution,
            )
        )
    return contacts


def _center_xy(bead: Bead) -> tuple[float, float]:
    points = bead.points
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _normal_2d(dx: float, dy: float) -> tuple[float, float]:
    norm = math.hypot(dx, dy)
    if norm <= 1e-12:
        return (1.0, 0.0)
    return (dx / norm, dy / norm)


def _closed(points: list[tuple[float, float, float]]) -> bool:
    if len(points) < 4:
        return False
    a, b = points[0], points[-1]
    return math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) <= 1e-9
