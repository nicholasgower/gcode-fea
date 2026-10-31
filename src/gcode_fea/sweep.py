"""Swept bead mesh generation and mesh statistics."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

import numpy as np

from .model import Bead, SliceModel

Array = np.ndarray


@dataclass
class Mesh:
    vertices: Array
    faces: Array

    @property
    def n_vertices(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def n_faces(self) -> int:
        return int(self.faces.shape[0])

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
        if self.n_vertices == 0:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        lo = self.vertices.min(axis=0)
        hi = self.vertices.max(axis=0)
        return (float(lo[0]), float(lo[1]), float(lo[2]), float(hi[0]), float(hi[1]), float(hi[2]))

    @property
    def volume(self) -> float:
        if self.n_faces == 0:
            return 0.0
        v0 = self.vertices[self.faces[:, 0]]
        v1 = self.vertices[self.faces[:, 1]]
        v2 = self.vertices[self.faces[:, 2]]
        return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)

    def watertight(self) -> tuple[bool, int]:
        """Return (is_watertight, boundary_edge_count)."""
        if self.n_faces == 0:
            return (False, 0)
        edges: dict[tuple[int, int], int] = {}
        for a, b, c in self.faces:
            for u, v in ((int(a), int(b)), (int(b), int(c)), (int(c), int(a))):
                key = (u, v) if u < v else (v, u)
                edges[key] = edges.get(key, 0) + 1
        boundary = sum(1 for count in edges.values() if count != 2)
        return (boundary == 0, boundary)


def mesh_bead(bead: Bead, join: str = "segments") -> Mesh:
    if join == "miter":
        return _mesh_miter(bead)
    if join != "segments":
        raise ValueError(f"unknown join mode: {join}")
    return _mesh_segments(bead)


def _mesh_segments(bead: Bead) -> Mesh:
    """Build a bead as a compound of watertight straight segment prisms.

    This is the default Phase 1 join model. Each segment conserves
    `area_per_length * segment_length`; adjacent segment prisms overlap at
    corners and their end caps are internal. Fusing them is a downstream
    operation.
    """

    profile = np.asarray(bead.cross_section.profile, dtype=float)
    if profile.ndim != 2 or profile.shape[0] < 3:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))
    points = list(bead.points)
    if len(points) < 2:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))

    closed = _closed(points)
    if closed:
        points = points[:-1]
        if len(points) < 3:
            closed = False
    segments = list(zip(points, points[1:]))
    if closed:
        segments.append((points[-1], points[0]))

    all_vertices: list[Array] = []
    all_faces: list[Array] = []
    offset = 0
    for start, end in segments:
        prism = _prism(
            np.asarray(start, dtype=float),
            np.asarray(end, dtype=float),
            profile,
        )
        if prism is None:
            continue
        vertices, faces = prism
        all_vertices.append(vertices)
        all_faces.append(faces + offset)
        offset += vertices.shape[0]
    if not all_vertices:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))
    return Mesh(np.vstack(all_vertices), np.vstack(all_faces))


def _prism(start: Array, end: Array, profile: Array) -> tuple[Array, Array] | None:
    tangent = end - start
    if float(np.linalg.norm(tangent)) <= 1e-12:
        return None
    tangent = _normalize(tangent)
    ring_start = _ring(start, tangent, profile)
    ring_end = _ring(end, tangent, profile)
    n_profile = profile.shape[0]
    start_center = 2 * n_profile
    end_center = start_center + 1
    vertices = np.vstack(
        [
            ring_start,
            ring_end,
            ring_start.mean(axis=0, keepdims=True),
            ring_end.mean(axis=0, keepdims=True),
        ]
    )
    faces: list[tuple[int, int, int]] = []
    for j in range(n_profile):
        j_next = (j + 1) % n_profile
        faces.append((j, j_next, n_profile + j_next))
        faces.append((j, n_profile + j_next, n_profile + j))
    for j in range(n_profile):
        j_next = (j + 1) % n_profile
        faces.append((start_center, j_next, j))
        faces.append((end_center, n_profile + j, n_profile + j_next))
    faces_array = np.asarray(faces, dtype=np.int64)
    mesh = Mesh(vertices, faces_array)
    if mesh.volume < 0:
        faces_array = faces_array[:, [0, 2, 1]]
    return vertices, faces_array


def _mesh_miter(bead: Bead) -> Mesh:
    profile = np.asarray(bead.cross_section.profile, dtype=float)
    if profile.ndim != 2 or profile.shape[0] < 3:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))

    points = list(bead.points)
    if len(points) < 2:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))

    closed = _closed(points)
    if closed:
        points = points[:-1]
        if len(points) < 3:
            closed = False

    tangents = _vertex_tangents(points, closed)
    rings = [_ring(np.asarray(point, dtype=float), tangent, profile) for point, tangent in zip(points, tangents)]

    vertices: list[Array] = rings
    faces: list[tuple[int, int, int]] = []
    n_profile = profile.shape[0]
    n_rings = len(rings)
    segment_count = n_rings if closed else n_rings - 1

    for i in range(segment_count):
        a_ring = rings[i]
        b_ring = rings[(i + 1) % n_rings]
        a_offset = i * n_profile
        b_offset = ((i + 1) % n_rings) * n_profile
        for j in range(n_profile):
            j_next = (j + 1) % n_profile
            a = a_offset + j
            b = a_offset + j_next
            c = b_offset + j_next
            d = b_offset + j
            faces.append((a, b, c))
            faces.append((a, c, d))

    if not closed:
        start_center = n_rings * n_profile
        end_center = start_center + 1
        # Compute both centers before appending: `vertices` aliases `rings`, so
        # appending the start center would otherwise change `rings[-1]`.
        start_point = rings[0].mean(axis=0, keepdims=True)
        end_point = rings[-1].mean(axis=0, keepdims=True)
        vertices.append(start_point)
        vertices.append(end_point)
        start_offset = 0
        end_offset = (n_rings - 1) * n_profile
        for j in range(n_profile):
            j_next = (j + 1) % n_profile
            faces.append((start_center, start_offset + j_next, start_offset + j))
            faces.append((end_center, end_offset + j, end_offset + j_next))

    vertices_array = np.vstack(vertices)
    faces_array = np.asarray(faces, dtype=np.int64)
    mesh = Mesh(vertices_array, faces_array)

    if mesh.volume < 0:
        mesh = Mesh(mesh.vertices, mesh.faces[:, [0, 2, 1]])
    return mesh


def mesh_model(model: SliceModel, join: str = "segments") -> Mesh:
    meshes = [mesh_bead(bead, join) for bead in model.sorted_beads()]
    meshes = [mesh for mesh in meshes if mesh.n_faces > 0]
    if not meshes:
        return Mesh(np.empty((0, 3), dtype=float), np.empty((0, 3), dtype=np.int64))
    vertices: list[Array] = []
    faces: list[Array] = []
    offset = 0
    for mesh in meshes:
        vertices.append(mesh.vertices)
        faces.append(mesh.faces + offset)
        offset += mesh.n_vertices
    return Mesh(np.vstack(vertices), np.vstack(faces))


def mesh_stats(model: SliceModel, join: str = "segments") -> dict:
    bead_stats = []
    total_mesh_volume = 0.0
    watertight_count = 0
    bead_count = 0
    for bead in model.sorted_beads():
        mesh = mesh_bead(bead, join)
        watertight, boundary_edges = mesh.watertight()
        if mesh.n_faces:
            bead_count += 1
        if watertight:
            watertight_count += 1
        total_mesh_volume += mesh.volume
        bead_stats.append(
            {
                "id": bead.bead_id,
                "analytic_volume": bead.volume,
                "mesh_volume": mesh.volume,
                "vertices": mesh.n_vertices,
                "faces": mesh.n_faces,
                "watertight": watertight,
                "boundary_edges": boundary_edges,
            }
        )
    return {
        "bead_count": bead_count,
        "watertight_beads": watertight_count,
        "watertight_fraction": (watertight_count / bead_count) if bead_count else 0.0,
        "analytic_volume": model.volume,
        "mesh_volume": total_mesh_volume,
        "beads": bead_stats,
    }


def _closed(points: list[tuple[float, float, float]]) -> bool:
    if len(points) < 4:
        return False
    a, b = points[0], points[-1]
    return hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) <= 1e-9


def _vertex_tangents(
    points: list[tuple[float, float, float]], closed: bool
) -> list[Array]:
    n = len(points)
    tangents: list[Array] = []
    for i in range(n):
        if closed:
            prev = np.asarray(points[(i - 1) % n], dtype=float)
            cur = np.asarray(points[i], dtype=float)
            nxt = np.asarray(points[(i + 1) % n], dtype=float)
            incoming = cur - prev
            outgoing = nxt - cur
            tangent = _normalize(incoming) + _normalize(outgoing)
        elif i == 0:
            tangent = np.asarray(points[1], dtype=float) - np.asarray(points[0], dtype=float)
        elif i == n - 1:
            tangent = np.asarray(points[-1], dtype=float) - np.asarray(points[-2], dtype=float)
        else:
            prev = np.asarray(points[i - 1], dtype=float)
            cur = np.asarray(points[i], dtype=float)
            nxt = np.asarray(points[i + 1], dtype=float)
            tangent = _normalize(cur - prev) + _normalize(nxt - cur)
        tangent = _normalize(tangent)
        if np.linalg.norm(tangent[:2]) <= 1e-12:
            tangent = np.array([1.0, 0.0, 0.0])
        tangents.append(tangent)
    return tangents


def _ring(center: Array, tangent: Array, profile: Array) -> Array:
    tx, ty = float(tangent[0]), float(tangent[1])
    norm = hypot(tx, ty)
    if norm <= 1e-12:
        tx, ty, norm = 1.0, 0.0, 1.0
    nx, ny = -ty / norm, tx / norm
    y = profile[:, 0][:, None]
    z = profile[:, 1][:, None]
    n = np.array([nx, ny, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    return center[None, :] + y * n[None, :] + z * up[None, :]


def _normalize(vector: Array) -> Array:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return np.zeros(3, dtype=float)
    return vector / norm
