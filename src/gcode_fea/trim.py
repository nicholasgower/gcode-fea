"""Trim stroke overlaps at wall boundaries.

Slicers intentionally over-extrude where infill meets a wall, so the bead
centerline can end inside the wall's swept footprint. The result is a visible
interpenetration: the infill bead's end cap cuts through the wall's side face.

This module clips non-wall bead paths against the wall footprint in each layer,
stopping them at the wall surface plus half the infill width. The bodies stay
separate (bead identity is preserved), but the overlap geometry is removed.

The operation changes volume, so it is a viewing/STEP option, not the default
for FEA exports. It requires Shapely.
"""

from __future__ import annotations

from dataclasses import replace
from .model import Bead, SliceModel


def trim_model(
    model: SliceModel,
    wall_keyword: str = "wall",
    min_length: float = 1e-6,
    buffer_resolution: int = 8,
) -> tuple[SliceModel, dict]:
    """Clip non-wall beads against the wall footprint of their layer."""

    walls = [
        bead for bead in model.beads if wall_keyword in bead.role.lower()
    ]
    if not walls:
        return model, _report(model, model, 0, 0, 0, 0)

    try:
        from shapely.geometry import LineString
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "trim_overlaps requires Shapely; install `shapely>=2.0` or "
            "disable trim_overlaps"
        ) from exc

    layers = model.layers()
    result: list[Bead] = []
    trimmed = 0
    dropped = 0
    created = 0
    for layer_beads in layers.values():
        layer_walls = [
            bead for bead in layer_beads if wall_keyword in bead.role.lower()
        ]
        if not layer_walls:
            result.extend(layer_beads)
            continue

        wall_region = unary_union(
            [
                _line(bead, LineString).buffer(
                    bead.cross_section.width_total / 2.0,
                    resolution=buffer_resolution,
                    cap_style="flat",
                    join_style="mitre",
                )
                for bead in layer_walls
            ]
        )
        interior = None
        try:
            from shapely.geometry import Polygon

            polygons = []
            for wall in layer_walls:
                line = _line(wall, LineString)
                if line.is_closed and line.is_ring:
                    polygon = Polygon(line)
                    if polygon.area > 1e-9:
                        polygons.append(polygon)
            if polygons:
                interior = unary_union(polygons)
        except Exception:
            interior = None

        for bead in layer_beads:
            if wall_keyword in bead.role.lower():
                result.append(bead)
                continue
            line = _line(bead, LineString)
            half_width = bead.cross_section.width_total / 2.0
            keep_out = (
                wall_region.buffer(
                    half_width,
                    resolution=buffer_resolution,
                    join_style="mitre",
                )
                if half_width > 0
                else wall_region
            )
            if not line.intersects(keep_out):
                result.append(bead)
                continue
            remaining = line.difference(keep_out)
            pieces = list(remaining.geoms) if hasattr(remaining, "geoms") else [remaining]
            pieces = [
                piece
                for piece in pieces
                if getattr(piece, "length", 0.0) >= min_length
            ]
            if interior is not None:
                pieces = [
                    piece
                    for piece in pieces
                    if interior.covers(
                        piece.interpolate(0.5, normalized=True)
                    )
                ]
            if not pieces:
                dropped += 1
                continue
            pieces.sort(key=lambda piece: line.project(piece.centroid))
            z = bead.points[0][2]
            produced = 0
            for index, piece in enumerate(pieces):
                coordinates = list(piece.coords)
                if len(coordinates) < 2:
                    continue
                points = tuple((float(x), float(y), z) for x, y in coordinates)
                result.append(
                    replace(
                        bead,
                        bead_id=f"{bead.bead_id}_T{index:03d}",
                        points=points,
                    )
                )
                produced += 1
            if produced:
                trimmed += 1
                created += produced

    trimmed_model = SliceModel(beads=result, meta=dict(model.meta))
    trimmed_model.meta["trim"] = _report(
        model, trimmed_model, len(walls), trimmed, dropped, created
    )
    return trimmed_model, trimmed_model.meta["trim"]


def _line(bead: Bead, line_type):
    return line_type([(point[0], point[1]) for point in bead.points])


def _report(
    before: SliceModel,
    after: SliceModel,
    wall_count: int,
    trimmed: int,
    dropped: int,
    created: int,
) -> dict:
    return {
        "enabled": True,
        "wall_beads": wall_count,
        "trimmed_beads": trimmed,
        "dropped_beads": dropped,
        "split_beads": created,
        "volume_before": before.volume,
        "volume_after": after.volume,
        "volume_relative_change": (
            abs(after.volume - before.volume) / before.volume
            if before.volume > 0
            else 0.0
        ),
    }
