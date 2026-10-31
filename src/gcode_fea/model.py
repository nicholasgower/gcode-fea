"""Core bead and slice data model for GCode-FEA."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Iterable


Point3 = tuple[float, float, float]
Point2 = tuple[float, float]


@dataclass(frozen=True)
class CrossSection:
    """A parameterized bead cross-section in the local (y, z) plane."""

    mode: str
    height: float
    area: float
    width_flat: float
    width_total: float
    flat_side_height: float
    sagitta: float
    radius: float | None
    profile: tuple[Point2, ...]
    warnings: tuple[str, ...] = ()

    @property
    def flat_top_fraction(self) -> float:
        if self.width_total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.width_flat / self.width_total))

    @property
    def flat_side_fraction(self) -> float:
        if self.height <= 0:
            return 0.0
        return max(0.0, min(1.0, self.flat_side_height / self.height))


@dataclass(frozen=True)
class Bead:
    """One extrusion path and its swept cross-section."""

    bead_id: str
    object_id: int
    layer_index: int
    print_z: float
    role: str
    points: tuple[Point3, ...]
    nominal_width: float
    height: float
    area_per_length: float
    cross_section: CrossSection
    material: str = "default"
    path_index: int = 0
    source: str = ""

    @property
    def length(self) -> float:
        return path_length(self.points)

    @property
    def volume(self) -> float:
        return self.cross_section.area * self.length

    @property
    def bottom_z(self) -> float:
        return min(point[2] for point in self.points)

    @property
    def top_z(self) -> float:
        return max(point[2] for point in self.points) + self.height

    def bbox(self) -> tuple[float, float, float, float, float, float]:
        """Approximate XY/Z bounding box from the centerline and width."""
        half = self.cross_section.width_total / 2.0
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return (
            min(xs) - half,
            min(ys) - half,
            min(point[2] for point in self.points),
            max(xs) + half,
            max(ys) + half,
            max(point[2] for point in self.points) + self.height,
        )


@dataclass
class Contact:
    id_a: str
    id_b: str
    kind: str  # "intralayer" or "interlayer"
    area: float
    normal: Point3
    centroid: Point3
    overlap: float = 0.0
    gap: float = 0.0
    resolution: float = 0.0


@dataclass
class SliceModel:
    beads: list[Bead] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def layers(self) -> dict[int, list[Bead]]:
        result: dict[int, list[Bead]] = {}
        for bead in self.beads:
            result.setdefault(bead.layer_index, []).append(bead)
        for layer in result.values():
            layer.sort(key=lambda bead: bead.bead_id)
        return dict(sorted(result.items()))

    def bbox(self) -> tuple[float, float, float, float, float, float]:
        if not self.beads:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        boxes = [bead.bbox() for bead in self.beads]
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            min(box[2] for box in boxes),
            max(box[3] for box in boxes),
            max(box[4] for box in boxes),
            max(box[5] for box in boxes),
        )

    @property
    def volume(self) -> float:
        return sum(bead.volume for bead in self.beads)

    def sorted_beads(self) -> list[Bead]:
        return sorted(self.beads, key=lambda bead: bead.bead_id)

    def by_id(self) -> dict[str, Bead]:
        return {bead.bead_id: bead for bead in self.beads}

    def filter_roi(
        self,
        bbox: tuple[float, float, float, float, float, float] | None = None,
        layer_range: tuple[int, int] | None = None,
    ) -> "SliceModel":
        beads: list[Bead] = []
        for bead in self.beads:
            if layer_range is not None and not (layer_range[0] <= bead.layer_index <= layer_range[1]):
                continue
            if bbox is not None and not _boxes_intersect(bead.bbox(), bbox):
                continue
            beads.append(bead)
        return SliceModel(beads=beads, meta=dict(self.meta))


def path_length(points: Iterable[Point3]) -> float:
    pts = list(points)
    if len(pts) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2])
    return total


def path_segments(points: Iterable[Point3]) -> list[tuple[Point3, Point3]]:
    pts = list(points)
    return list(zip(pts, pts[1:]))


def _boxes_intersect(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
) -> bool:
    return not (
        a[3] < b[0]
        or b[3] < a[0]
        or a[4] < b[1]
        or b[4] < a[1]
        or a[5] < b[2]
        or b[5] < a[2]
    )
