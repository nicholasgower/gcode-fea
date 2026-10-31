"""Boolean-union STEP export: merge overlapping beads into one body.

The planar writer keeps one body per bead. When beads overlap (infill ends into
a wall, adjacent strokes, stacked layers), that overlap is visible as
interpenetrating faces. This module unions the bead solids with Manifold3D and
writes one AP214 solid per connected result.

The union is a triangle B-rep: each triangle becomes a planar ``ADVANCED_FACE``
with shared vertices and edges. That is valid STEP and OCCT-readable, but it is
larger than the analytic per-bead writer. Use a profile tolerance, an ROI, or a
layer range to keep it manageable.

Requires ``manifold3d`` (Apache-2.0); it is a plugin dependency.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .model import SliceModel
from .step_planar import (
    _CONTEXT_ENTITY_COUNT,
    _build_segment_solids,
    _header,
    _num,
    _patch_representation,
    simplify_profile,
)

Point3 = tuple[float, float, float]


def write_step_merged(
    model: SliceModel,
    path: str | Path,
    profile_stride: int = 1,
    tolerance: float = 0.0,
    simplify: float = 0.0,
    max_triangles: int = 60_000,
) -> dict:
    """Union all overlapping bead solids and write merged STEP bodies."""

    try:
        import manifold3d as m3
    except ImportError as exc:
        raise RuntimeError(
            "merged STEP export requires manifold3d; install "
            "`manifold3d>=3.5` or use step_engine='planar'"
        ) from exc

    if not model.beads:
        raise ValueError("cannot export an empty model")

    manifolds = []
    for bead in model.sorted_beads():
        profile = list(bead.cross_section.profile)
        if tolerance > 0:
            profile = simplify_profile(profile, tolerance)
        if profile_stride > 1:
            profile = profile[::profile_stride]
        if len(profile) < 3:
            continue
        # Use the convex per-segment prisms. The stitched/mitred bead solids
        # can self-intersect at sharp closed-loop corners; Manifold then splits
        # them into fragments. Segment prisms are individually valid and the
        # boolean union removes their overlaps.
        for solid in _build_segment_solids(bead, profile):
            manifold = _solid_to_manifold(m3, solid)
            if manifold is not None:
                manifolds.append(manifold)
    if not manifolds:
        raise ValueError("no bead solids could be converted for merging")

    merged = m3.Manifold.batch_boolean(manifolds, m3.OpType.Add)
    if simplify > 0:
        merged = merged.simplify(simplify)
    mesh = merged.to_mesh()
    # Do not weld by coordinate: Manifold's mesh is already closed and its
    # duplicate coordinates are seams between touching shells. Welding them
    # creates non-manifold edges and open B-reps.
    vertices = np.asarray(mesh.vert_properties, dtype=float)
    triangles = np.asarray(mesh.tri_verts, dtype=np.int64)
    vertices = _inflate_degenerate(vertices, triangles)
    if triangles.size == 0:
        raise ValueError("boolean union produced no triangles")
    if len(triangles) > max_triangles:
        raise ValueError(
            f"merged STEP needs {len(triangles):,} triangles, above the "
            f"limit of {max_triangles:,}. Use an ROI, a layer range, a larger "
            "step_tolerance, or raise max_step_triangles."
        )

    components = _components(triangles)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = _header(target.stem or "gcode_fea")
    next_id = _CONTEXT_ENTITY_COUNT + 1
    solid_ids: list[int] = []
    for component in components:
        solid_id, next_id = _emit_component(
            lines, vertices, triangles, component, next_id
        )
        solid_ids.append(solid_id)
    _patch_representation(lines, solid_ids)
    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    target.write_text("\n".join(lines) + "\n")

    volume = sum(_component_volume(vertices, triangles, c) for c in components)
    open_edges = sum(
        _open_edge_count(triangles, component) for component in components
    )
    analytic = model.volume
    return {
        "path": str(target),
        "engine": "merged",
        "solids": len(components),
        "triangles": len(triangles),
        "vertices": len(vertices),
        "volume": volume,
        "analytic_volume": analytic,
        "volume_relative_error": (
            abs(volume - analytic) / analytic if analytic > 0 else None
        ),
        "closed": open_edges == 0,
        "open_edges": open_edges,
        "merged_from_beads": len(model.beads),
    }


def _solid_to_manifold(m3, solid):
    triangles = []
    for face in solid.faces:
        for index in range(1, len(face) - 1):
            triangles.append((face[0], face[index], face[index + 1]))
    if not triangles:
        return None
    vertices = np.asarray(solid.points, dtype=np.float32)
    faces = np.asarray(triangles, dtype=np.uint32)
    try:
        return m3.Manifold(m3.Mesh(vert_properties=vertices, tri_verts=faces))
    except Exception:
        return None


def _inflate_degenerate(
    vertices: np.ndarray,
    triangles: np.ndarray,
    epsilon: float = 1e-5,
) -> np.ndarray:
    """Give zero-area triangles a tiny span so OCCT does not drop them.

    Manifold's boolean can leave collinear triangles at coplanar intersections.
    OCCT discards them, which opens the shell. Nudging one vertex by a
    sub-micrometre amount keeps the topology closed.
    """

    points = np.array(vertices, dtype=float, copy=True)
    for _ in range(4):
        degenerate = []
        for index, triangle in enumerate(triangles):
            a, b, c = points[triangle]
            if np.linalg.norm(np.cross(b - a, c - a)) / 2.0 < 1e-14:
                degenerate.append(index)
        if not degenerate:
            break
        for index in degenerate:
            triangle = triangles[index]
            best = None
            for position in range(3):
                u = int(triangle[position])
                v = int(triangle[(position + 1) % 3])
                opposite = int(triangle[(position + 2) % 3])
                length = float(np.linalg.norm(points[v] - points[u]))
                if best is None or length > best[0]:
                    best = (length, u, v, opposite)
            _, u, v, opposite = best
            edge = points[v] - points[u]
            axis = (
                np.array([0.0, 0.0, 1.0])
                if abs(edge[2]) < 0.9 * np.linalg.norm(edge)
                else np.array([1.0, 0.0, 0.0])
            )
            direction = np.cross(edge, axis)
            norm = float(np.linalg.norm(direction))
            if norm < 1e-12:
                direction = np.cross(edge, np.array([0.0, 1.0, 0.0]))
                norm = float(np.linalg.norm(direction))
            if norm > 1e-15:
                points[opposite] += direction / norm * epsilon
    return points


def _components(triangles: np.ndarray) -> list[list[int]]:
    """Triangle components joined across manifold (two-face) edges.

    A four-face edge is a pinch where two shells touch along a line. It is not
    a valid solid boundary, so it splits the mesh there; each side is exported
    as its own closed solid with duplicated edge vertices.
    """

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for index, triangle in enumerate(triangles):
        for position in range(3):
            u = int(triangle[position])
            v = int(triangle[(position + 1) % 3])
            key = (u, v) if u < v else (v, u)
            edge_faces.setdefault(key, []).append(index)

    parent = list(range(len(triangles)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for faces in edge_faces.values():
        if len(faces) == 2:
            union(faces[0], faces[1])

    groups: dict[int, list[int]] = {}
    for index in range(len(triangles)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def _open_edge_count(
    triangles: np.ndarray,
    component: list[int],
) -> int:
    counts: dict[tuple[int, int], int] = {}
    for index in component:
        triangle = triangles[index]
        for position in range(3):
            u = int(triangle[position])
            v = int(triangle[(position + 1) % 3])
            key = (u, v) if u < v else (v, u)
            counts[key] = counts.get(key, 0) + 1
    return sum(1 for count in counts.values() if count != 2)


def _component_volume(
    vertices: np.ndarray,
    triangles: np.ndarray,
    component: list[int],
) -> float:
    total = 0.0
    for index in component:
        a, b, c = triangles[index]
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        total += float(np.dot(pa, np.cross(pb, pc))) / 6.0
    return total


def _emit_component(
    lines: list[str],
    vertices: np.ndarray,
    triangles: np.ndarray,
    component: list[int],
    next_id: int,
) -> tuple[int, int]:
    if _component_volume(vertices, triangles, component) < 0:
        component = [
            (triangles[index][0], triangles[index][2], triangles[index][1])
            for index in component
        ]
    else:
        component = [tuple(int(v) for v in triangles[index]) for index in component]

    point_ids: dict[int, int] = {}
    vertex_ids: dict[int, int] = {}
    for triangle in component:
        for vertex in triangle:
            if vertex in point_ids:
                continue
            point_id = next_id
            next_id += 1
            vertex_id = next_id
            next_id += 1
            x, y, z = vertices[vertex]
            lines.append(
                f"#{point_id} = CARTESIAN_POINT('',"
                f"({_num(float(x))},{_num(float(y))},{_num(float(z))}));"
            )
            lines.append(f"#{vertex_id} = VERTEX_POINT('',#{point_id});")
            point_ids[vertex] = point_id
            vertex_ids[vertex] = vertex_id

    edge_ids: dict[tuple[int, int], tuple[int, int]] = {}
    for triangle in component:
        for index in range(3):
            u = triangle[index]
            v = triangle[(index + 1) % 3]
            key = (u, v) if u < v else (v, u)
            if key in edge_ids:
                continue
            start, end = key
            direction = _direction(vertices[end] - vertices[start])
            if direction is None:
                continue
            direction_id = next_id
            next_id += 1
            vector_id = next_id
            next_id += 1
            line_id = next_id
            next_id += 1
            edge_id = next_id
            next_id += 1
            lines.append(
                f"#{direction_id} = DIRECTION('',"
                f"({_num(direction[0])},{_num(direction[1])},{_num(direction[2])}));"
            )
            lines.append(f"#{vector_id} = VECTOR('',#{direction_id},1.);")
            lines.append(
                f"#{line_id} = LINE('',#{point_ids[start]},#{vector_id});"
            )
            lines.append(
                f"#{edge_id} = EDGE_CURVE('',#{vertex_ids[start]},"
                f"#{vertex_ids[end]},#{line_id},.T.);"
            )
            edge_ids[key] = (edge_id, start)

    face_ids: list[int] = []
    for triangle in component:
        oriented: list[int] = []
        for index in range(3):
            u = triangle[index]
            v = triangle[(index + 1) % 3]
            key = (u, v) if u < v else (v, u)
            edge = edge_ids.get(key)
            if edge is None:
                continue
            edge_id, canonical = edge
            flag = ".T." if u == canonical else ".F."
            oriented_id = next_id
            next_id += 1
            lines.append(
                f"#{oriented_id} = ORIENTED_EDGE('',*,*,#{edge_id},{flag});"
            )
            oriented.append(oriented_id)
        if len(oriented) != 3:
            continue
        loop_id = next_id
        next_id += 1
        bound_id = next_id
        next_id += 1
        lines.append(f"#{loop_id} = EDGE_LOOP('',({_refs(oriented)}));")
        lines.append(f"#{bound_id} = FACE_OUTER_BOUND('',#{loop_id},.T.);")

        a, b, c = (vertices[int(v)] for v in triangle)
        normal = _cross(b - a, c - a)
        unit_normal = _direction(normal)
        ref = _direction(b - a)
        if unit_normal is None or ref is None:
            continue
        normal_id = next_id
        next_id += 1
        ref_id = next_id
        next_id += 1
        axis_id = next_id
        next_id += 1
        plane_id = next_id
        next_id += 1
        face_id = next_id
        next_id += 1
        lines.append(
            f"#{normal_id} = DIRECTION('',"
            f"({_num(unit_normal[0])},{_num(unit_normal[1])},{_num(unit_normal[2])}));"
        )
        lines.append(
            f"#{ref_id} = DIRECTION('',"
            f"({_num(ref[0])},{_num(ref[1])},{_num(ref[2])}));"
        )
        lines.append(
            f"#{axis_id} = AXIS2_PLACEMENT_3D('',"
            f"#{point_ids[int(triangle[0])]},#{normal_id},#{ref_id});"
        )
        lines.append(f"#{plane_id} = PLANE('',#{axis_id});")
        lines.append(
            f"#{face_id} = ADVANCED_FACE('',(#{bound_id}),#{plane_id},.T.);"
        )
        face_ids.append(face_id)

    shell_id = next_id
    next_id += 1
    solid_id = next_id
    next_id += 1
    lines.append(f"#{shell_id} = CLOSED_SHELL('',({_refs(face_ids)}));")
    lines.append(f"#{solid_id} = MANIFOLD_SOLID_BREP('',#{shell_id});")
    return solid_id, next_id


def _direction(vector) -> Point3 | None:
    length = math.sqrt(
        float(vector[0]) ** 2 + float(vector[1]) ** 2 + float(vector[2]) ** 2
    )
    if length <= 1e-15:
        return None
    return (
        float(vector[0]) / length,
        float(vector[1]) / length,
        float(vector[2]) / length,
    )


def _cross(a, b):
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _refs(ids: list[int]) -> str:
    return ",".join(f"#{entity}" for entity in ids)
