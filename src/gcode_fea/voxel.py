"""Voxel hexahedral mesh of the bead model.

The paper's simulations use 8-node solid elements (Ansys SOLID185). This module
builds an equivalent-thickness hex mesh by sampling the swept bead solids on a
regular grid. It is deliberately simple:

- every occupied voxel becomes one C3D8/HEX8 element,
- elements carry the bead ID, layer, role, and material as sets,
- shared faces between elements of different beads become interface patches.

The mesh is an approximation of the segment-prism compound: surfaces are
staircased at the voxel size, and joints between bead segments are not
reconstructed. Volume error is reported so the user can choose the resolution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .model import Bead, SliceModel

Array = np.ndarray

#: Abaqus/CalculiX C3D8 node ordering, as local indices into the element row.
HEX_FACES = (
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)


@dataclass
class InterfacePatch:
    """A set of coplanar quad faces shared by two beads."""

    id_a: str
    id_b: str
    kind: str
    area: float
    faces: list[tuple[int, int, int, int]] = field(default_factory=list)
    normal: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def to_dict(self) -> dict:
        return {
            "a": self.id_a,
            "b": self.id_b,
            "kind": self.kind,
            "area": self.area,
            "face_count": len(self.faces),
            "normal": list(self.normal),
        }


@dataclass
class VoxelMesh:
    nodes: Array
    elements: Array
    element_bead: Array
    bead_ids: tuple[str, ...]
    resolution: float
    origin: tuple[float, float, float]
    element_layer: Array
    element_role: Array
    meta: dict = field(default_factory=dict)

    @property
    def n_nodes(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def n_elements(self) -> int:
        return int(self.elements.shape[0])

    @property
    def volume(self) -> float:
        return self.n_elements * self.resolution ** 3

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
        if self.n_nodes == 0:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        lo = self.nodes.min(axis=0)
        hi = self.nodes.max(axis=0)
        return (
            float(lo[0]),
            float(lo[1]),
            float(lo[2]),
            float(hi[0]),
            float(hi[1]),
            float(hi[2]),
        )

    def element_sets(self) -> dict[str, Array]:
        """Named element sets as 0-based element indices."""

        sets: dict[str, Array] = {
            "EALL": np.arange(self.n_elements, dtype=np.int64)
        }
        for index, bead_id in enumerate(self.bead_ids):
            sets[f"BEAD_{_sanitize(bead_id)}"] = np.flatnonzero(
                self.element_bead == index
            ).astype(np.int64)
        for layer in np.unique(self.element_layer):
            sets[f"LAYER_{int(layer):04d}"] = np.flatnonzero(
                self.element_layer == layer
            ).astype(np.int64)
        for role in np.unique(self.element_role):
            sets[f"ROLE_{_sanitize(str(role))}"] = np.flatnonzero(
                self.element_role == role
            ).astype(np.int64)
        return sets

    def summary(self) -> dict:
        return {
            "resolution": self.resolution,
            "nodes": self.n_nodes,
            "elements": self.n_elements,
            "beads": len(self.bead_ids),
            "layers": int(len(np.unique(self.element_layer))),
            "volume": self.volume,
            "bbox": list(self.bbox),
        }


def voxelize(
    model: SliceModel,
    resolution: float = 0.2,
    bead_ids: set[str] | None = None,
) -> VoxelMesh:
    """Rasterize the bead model into a conforming hex8 mesh."""

    if resolution <= 0:
        raise ValueError("resolution must be positive")
    if not model.beads:
        raise ValueError("cannot voxelize an empty model")

    beads = [
        bead
        for bead in model.sorted_beads()
        if bead_ids is None or bead.bead_id in bead_ids
    ]
    if not beads:
        raise ValueError("bead_ids selected no beads")

    xmin, ymin, zmin, xmax, ymax, zmax = _model_bbox(beads)
    origin = (
        xmin - resolution,
        ymin - resolution,
        zmin - resolution,
    )
    nx = int(math.ceil((xmax - xmin) / resolution)) + 3
    ny = int(math.ceil((ymax - ymin) / resolution)) + 3
    nz = int(math.ceil((zmax - zmin) / resolution)) + 3

    occupancy: dict[tuple[int, int, int], int] = {}
    for bead_index, bead in enumerate(beads):
        _rasterize_bead(
            occupancy,
            bead,
            bead_index,
            origin,
            resolution,
            nx,
            ny,
            nz,
        )
    if not occupancy:
        raise ValueError(
            "voxelization produced no elements; use a resolution smaller than "
            "the bead height and strand width"
        )

    keys = sorted(occupancy)
    node_index: dict[tuple[int, int, int], int] = {}
    node_coords: list[tuple[float, float, float]] = []

    def node_id(i: int, j: int, k: int) -> int:
        key = (i, j, k)
        existing = node_index.get(key)
        if existing is not None:
            return existing
        index = len(node_coords)
        node_index[key] = index
        node_coords.append(
            (
                origin[0] + i * resolution,
                origin[1] + j * resolution,
                origin[2] + k * resolution,
            )
        )
        return index

    elements: list[tuple[int, ...]] = []
    element_bead: list[int] = []
    element_layer: list[int] = []
    element_role: list[str] = []
    for i, j, k in keys:
        elements.append(
            (
                node_id(i, j, k),
                node_id(i + 1, j, k),
                node_id(i + 1, j + 1, k),
                node_id(i, j + 1, k),
                node_id(i, j, k + 1),
                node_id(i + 1, j, k + 1),
                node_id(i + 1, j + 1, k + 1),
                node_id(i, j + 1, k + 1),
            )
        )
        bead = beads[occupancy[(i, j, k)]]
        element_bead.append(occupancy[(i, j, k)])
        element_layer.append(bead.layer_index)
        element_role.append(bead.role)

    return VoxelMesh(
        nodes=np.asarray(node_coords, dtype=float),
        elements=np.asarray(elements, dtype=np.int64),
        element_bead=np.asarray(element_bead, dtype=np.int64),
        bead_ids=tuple(bead.bead_id for bead in beads),
        resolution=resolution,
        origin=origin,
        element_layer=np.asarray(element_layer, dtype=np.int64),
        element_role=np.asarray(element_role, dtype=object),
        meta={
            "volume_analytic": model.volume,
            "source": model.meta.get("source", "unknown"),
        },
    )


def interface_patches(mesh: VoxelMesh) -> list[InterfacePatch]:
    """Shared faces between elements of different beads, grouped by pair."""

    face_map: dict[tuple[int, ...], int] = {}
    patches: dict[tuple[int, int], InterfacePatch] = {}
    nodes = mesh.elements
    bead_of = mesh.element_bead
    for element_index, element in enumerate(nodes):
        for face in HEX_FACES:
            quad = tuple(int(element[i]) for i in face)
            key = tuple(sorted(quad))
            other = face_map.get(key)
            if other is None:
                face_map[key] = element_index
                continue
            if other == element_index or bead_of[other] == bead_of[element_index]:
                continue
            a, b = sorted((int(bead_of[other]), int(bead_of[element_index])))
            patch = patches.get((a, b))
            if patch is None:
                bead_a = mesh.bead_ids[a]
                bead_b = mesh.bead_ids[b]
                kind = (
                    "interlayer"
                    if mesh.element_layer[other] != mesh.element_layer[element_index]
                    else "intralayer"
                )
                patch = InterfacePatch(
                    id_a=bead_a,
                    id_b=bead_b,
                    kind=kind,
                    area=0.0,
                    normal=_face_normal(mesh, quad),
                )
                patches[(a, b)] = patch
            patch.faces.append(quad)
            patch.area += mesh.resolution ** 2

    return [
        patch
        for _, patch in sorted(
            patches.items(), key=lambda item: (item[1].kind, item[1].id_a, item[1].id_b)
        )
    ]


def write_mesh_summary(mesh: VoxelMesh, path: str | Path) -> Path:
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "gcode_fea.voxel_mesh.v1",
        "summary": mesh.summary(),
        "meta": mesh.meta,
        "volume_relative_error": _volume_error(mesh),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def _volume_error(mesh: VoxelMesh) -> float | None:
    analytic = mesh.meta.get("volume_analytic")
    if not analytic:
        return None
    return abs(mesh.volume - float(analytic)) / float(analytic)


def _rasterize_bead(
    occupancy: dict[tuple[int, int, int], int],
    bead: Bead,
    bead_index: int,
    origin: tuple[float, float, float],
    resolution: float,
    nx: int,
    ny: int,
    nz: int,
) -> None:
    profile = np.asarray(bead.cross_section.profile, dtype=float)
    if profile.ndim != 2 or profile.shape[0] < 3:
        return
    height = float(bead.cross_section.height)
    half_width = float(bead.cross_section.width_total) / 2.0
    z0 = float(min(point[2] for point in bead.points))

    for start, end in _segments(bead):
        x0, y0 = float(start[0]), float(start[1])
        x1, y1 = float(end[0]), float(end[1])
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        tx, ty = dx / length, dy / length
        px, py = -ty, tx

        i0 = _clamp(int(math.floor((min(x0, x1) - half_width - origin[0]) / resolution)), 0, nx - 1)
        i1 = _clamp(int(math.ceil((max(x0, x1) + half_width - origin[0]) / resolution)), 0, nx - 1)
        j0 = _clamp(int(math.floor((min(y0, y1) - half_width - origin[1]) / resolution)), 0, ny - 1)
        j1 = _clamp(int(math.ceil((max(y0, y1) + half_width - origin[1]) / resolution)), 0, ny - 1)
        k0 = _clamp(int(math.floor((z0 - origin[2]) / resolution)), 0, nz - 1)
        k1 = _clamp(int(math.ceil((z0 + height - origin[2]) / resolution)), 0, nz - 1)
        if i1 < i0 or j1 < j0 or k1 < k0:
            continue

        xs = origin[0] + (np.arange(i0, i1 + 1) + 0.5) * resolution
        ys = origin[1] + (np.arange(j0, j1 + 1) + 0.5) * resolution
        zs = origin[2] + (np.arange(k0, k1 + 1) + 0.5) * resolution
        X, Y = np.meshgrid(xs, ys)
        lateral = (X - x0) * px + (Y - y0) * py
        along = (X - x0) * tx + (Y - y0) * ty
        planar = (along >= -1e-9) & (along <= length + 1e-9)
        for k in range(k0, k1 + 1):
            key_base = (k,)
            zrel = zs[k - k0] - z0
            inside = _points_in_polygon(lateral, zrel, profile) & planar
            if not inside.any():
                continue
            rows, cols = np.nonzero(inside)
            for row, col in zip(rows, cols):
                key = (i0 + int(col), j0 + int(row), int(key_base[0]))
                if key not in occupancy:
                    occupancy[key] = bead_index


def _points_in_polygon(px: Array, py: float, polygon: Array) -> Array:
    inside = np.zeros(px.shape, dtype=bool)
    n = polygon.shape[0]
    for index in range(n):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % n]
        if y1 == y2:
            continue
        crosses = (y1 > py) != (y2 > py)
        if not crosses:
            continue
        x_at = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (px < x_at)
    return inside


def _segments(bead: Bead):
    points = list(bead.points)
    if len(points) < 2:
        return []
    segments = list(zip(points, points[1:]))
    if len(points) >= 4:
        a, b = points[0], points[-1]
        if math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) <= 1e-9:
            trimmed = points[:-1]
            segments = list(zip(trimmed, trimmed[1:]))
            if len(trimmed) >= 3:
                segments.append((trimmed[-1], trimmed[0]))
    return segments


def _face_normal(mesh: VoxelMesh, quad: tuple[int, int, int, int]) -> tuple[float, float, float]:
    a, b, c = (
        mesh.nodes[quad[0]],
        mesh.nodes[quad[1]],
        mesh.nodes[quad[2]],
    )
    normal = np.cross(b - a, c - a)
    norm = float(np.linalg.norm(normal))
    if norm <= 1e-12:
        return (0.0, 0.0, 0.0)
    normal = normal / norm
    return (float(normal[0]), float(normal[1]), float(normal[2]))


def _model_bbox(beads: list[Bead]) -> tuple[float, float, float, float, float, float]:
    boxes = [bead.bbox() for bead in beads]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        max(box[4] for box in boxes),
        max(box[5] for box in boxes),
    )


def _sanitize(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in value)
    return cleaned.upper().strip("_") or "UNNAMED"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
