"""Pure-Python planar B-rep STEP export for bead compounds.

gmsh/OCCT gives the best STEP, but gmsh is GPL and is not bundled with the
plugin. This module writes a standard AP214 ``MANIFOLD_SOLID_BREP`` using only
the standard library: one closed solid per bead, with planar ``ADVANCED_FACE``
surfaces.

Rings are placed at every path vertex and adjacent segments share them, so the
old per-segment prisms and their internal overlapping caps disappear. Sharp
reversals keep the per-segment form. Curved profile sections can be simplified
with a chord tolerance (``tolerance``) to reduce polygon count.

The output is flat-faceted rather than an analytic swept surface. OCCT,
FreeCAD, PrePoMax, and other STEP viewers read this form.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .model import Bead, SliceModel

Point3 = tuple[float, float, float]

_CONTEXT_ENTITY_COUNT = 20
_SHAPE_REP_ID = 19


@dataclass
class PlanarSolid:
    """One closed bead body with a planar-faced shell."""

    points: list[Point3]
    faces: list[list[int]]

    @property
    def volume(self) -> float:
        total = 0.0
        for face in self.faces:
            origin = self.points[face[0]]
            for index in range(1, len(face) - 1):
                a = self.points[face[index]]
                b = self.points[face[index + 1]]
                total += _dot(origin, _cross(a, b)) / 6.0
        return total


def build_solids(
    bead: Bead,
    profile_stride: int = 1,
    tolerance: float = 0.0,
) -> list[PlanarSolid]:
    """Build one solid per bead by stitching rings at every path vertex.

    Adjacent segments share their miter ring, so the internal caps and
    overlapping segment prisms of the old writer disappear. The result is one
    closed body per bead. Sharp miter corners can lose or gain a little volume;
    the caller reports the error against the analytic bead volume.
    """

    profile = list(bead.cross_section.profile)
    if tolerance > 0:
        profile = simplify_profile(profile, tolerance)
    if profile_stride > 1:
        profile = profile[::profile_stride]
    if len(profile) < 3:
        return []

    points = list(bead.points)
    if len(points) < 2:
        return []
    closed = _closed(points)
    if closed:
        points = points[:-1]
        if len(points) < 3:
            closed = False
    if len(points) < 2:
        return []

    count = len(profile)
    tangents = _vertex_tangents(points, closed)
    scales = _miter_scales(points, tangents, closed)
    if scales is None:
        return _build_segment_solids(bead, profile)
    rings: list[list[Point3]] = []
    for point, tangent, scale in zip(points, tangents, scales):
        ring = _ring_points(point, tangent, profile, scale)
        if ring is not None:
            rings.append(ring)
    if len(rings) < 2:
        return []

    vertices = [position for ring in rings for position in ring]
    faces: list[list[int]] = []
    segment_count = len(rings) if closed else len(rings) - 1
    for segment in range(segment_count):
        a = segment * count
        b = ((segment + 1) % len(rings)) * count
        for index in range(count):
            following = (index + 1) % count
            faces.append([a + index, a + following, b + following, b + index])
    if not closed:
        faces.append(list(range(count)))
        faces.append(list(range((len(rings) - 1) * count, len(rings) * count)))

    _orient_faces_consistently(vertices, faces)
    faces = _planarize(vertices, faces)
    return [PlanarSolid(points=vertices, faces=faces)]


def simplify_profile(
    profile: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    """Reduce polygon count on curved sections within ``tolerance`` mm.

    The profile is a closed polygon, so it is split at the point farthest from
    the first point, each half is simplified with Ramer-Douglas-Peucker, and
    the halves are rejoined. Corners and the flat top/bottom survive.
    """

    if tolerance <= 0 or len(profile) <= 4:
        return profile
    points = list(profile)
    far = max(
        range(1, len(points)),
        key=lambda index: _distance2(points[0], points[index]),
    )
    first = _rdp(points[: far + 1], tolerance)
    second = _rdp(points[far:] + [points[0]], tolerance)
    merged = first[:-1] + second[:-1]
    if len(merged) < 3:
        return profile
    return merged


def estimate_faces(
    model: SliceModel,
    profile_stride: int = 1,
    tolerance: float = 0.0,
) -> int:
    """Exact face count for the planar writer, including corner triangles."""

    faces = 0
    for bead in model.beads:
        for solid in build_solids(bead, profile_stride, tolerance):
            faces += len(solid.faces)
    return faces


def write_step_planar(
    model: SliceModel,
    path: str | Path,
    profile_stride: int = 1,
    tolerance: float = 0.0,
    name: str = "gcode_fea",
) -> dict:
    """Write one stitched planar solid per bead as AP214 geometry.

    ``tolerance`` is the maximum chord deviation (mm) allowed when simplifying
    curved profile sections. Zero keeps every profile point.
    """

    if not model.beads:
        raise ValueError("cannot export an empty model")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    solids: list[PlanarSolid] = []
    dropped_solids = 0
    for bead in model.sorted_beads():
        bead_solids = build_solids(bead, profile_stride, tolerance)
        if not bead_solids:
            dropped_solids += 1
            continue
        solids.extend(bead_solids)
    if not solids:
        raise ValueError("no bead segments could be converted to solids")

    lines = _header(name)
    next_id = _CONTEXT_ENTITY_COUNT + 1
    solid_ids: list[int] = []
    face_count = 0
    point_count = 0
    open_edges = 0
    for prism in solids:
        solid_id, next_id = _emit_solid(lines, prism, next_id)
        solid_ids.append(solid_id)
        face_count += len(prism.faces)
        point_count += len(prism.points)
        open_edges += _open_edge_count(prism.faces)

    _patch_representation(lines, solid_ids)
    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    target.write_text("\n".join(lines) + "\n")

    step_volume = sum(prism.volume for prism in solids)
    analytic = model.volume
    return {
        "path": str(target),
        "engine": "planar",
        "solids": len(solids),
        "faces": face_count,
        "points": point_count,
        "volume": step_volume,
        "analytic_volume": analytic,
        "volume_relative_error": (
            abs(step_volume - analytic) / analytic if analytic > 0 else None
        ),
        "closed": open_edges == 0,
        "open_edges": open_edges,
        "bytes": target.stat().st_size,
        "dropped_solids": dropped_solids,
    }


def _open_edge_count(faces: list[list[int]]) -> int:
    """Edges of a shell not shared by exactly two faces."""

    counts: dict[tuple[int, int], int] = {}
    for face in faces:
        for index in range(len(face)):
            u = face[index]
            v = face[(index + 1) % len(face)]
            key = (u, v) if u < v else (v, u)
            counts[key] = counts.get(key, 0) + 1
    return sum(1 for count in counts.values() if count != 2)


def _emit_solid(
    lines: list[str],
    prism: PlanarSolid,
    next_id: int,
) -> tuple[int, int]:
    point_ids: list[int] = []
    vertex_ids: list[int] = []
    for point in prism.points:
        point_id = next_id
        next_id += 1
        vertex_id = next_id
        next_id += 1
        lines.append(
            f"#{point_id} = CARTESIAN_POINT('',"
            f"({_num(point[0])},{_num(point[1])},{_num(point[2])}));"
        )
        lines.append(f"#{vertex_id} = VERTEX_POINT('',#{point_id});")
        point_ids.append(point_id)
        vertex_ids.append(vertex_id)

    edge_map: dict[tuple[int, int], tuple[int, int]] = {}
    for face in prism.faces:
        for index in range(len(face)):
            u = face[index]
            v = face[(index + 1) % len(face)]
            key = (u, v) if u < v else (v, u)
            if key in edge_map:
                continue
            start, end = key
            direction = _unit(_sub(prism.points[end], prism.points[start]))
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
            edge_map[key] = (edge_id, start)

    face_ids: list[int] = []
    for face in prism.faces:
        oriented: list[int] = []
        for index in range(len(face)):
            u = face[index]
            v = face[(index + 1) % len(face)]
            key = (u, v) if u < v else (v, u)
            edge = edge_map.get(key)
            if edge is None:
                continue
            edge_id, canonical_start = edge
            orientation = ".T." if u == canonical_start else ".F."
            oriented_id = next_id
            next_id += 1
            lines.append(
                f"#{oriented_id} = ORIENTED_EDGE('',*,*,#{edge_id},{orientation});"
            )
            oriented.append(oriented_id)
        if len(oriented) < 3:
            continue
        loop_id = next_id
        next_id += 1
        bound_id = next_id
        next_id += 1
        lines.append(
            f"#{loop_id} = EDGE_LOOP('',({_refs(oriented)}));"
        )
        lines.append(f"#{bound_id} = FACE_OUTER_BOUND('',#{loop_id},.T.);")

        normal = _newell(prism.points, face)
        if normal is None:
            continue
        normal = _unit(normal)
        if normal is None:
            continue
        ref = _reference_direction(prism.points, face, normal)
        normal_dir_id = next_id
        next_id += 1
        ref_dir_id = next_id
        next_id += 1
        axis_id = next_id
        next_id += 1
        plane_id = next_id
        next_id += 1
        face_id = next_id
        next_id += 1
        lines.append(
            f"#{normal_dir_id} = DIRECTION('',"
            f"({_num(normal[0])},{_num(normal[1])},{_num(normal[2])}));"
        )
        lines.append(
            f"#{ref_dir_id} = DIRECTION('',"
            f"({_num(ref[0])},{_num(ref[1])},{_num(ref[2])}));"
        )
        lines.append(
            f"#{axis_id} = AXIS2_PLACEMENT_3D('',#{point_ids[face[0]]},"
            f"#{normal_dir_id},#{ref_dir_id});"
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


def _segments(bead: Bead) -> list[tuple[Point3, Point3]]:
    points = list(bead.points)
    if len(points) < 2:
        return []
    segments = list(zip(points, points[1:]))
    if len(points) >= 4:
        a, b = points[0], points[-1]
        if math.dist(a, b) <= 1e-9:
            trimmed = points[:-1]
            segments = list(zip(trimmed, trimmed[1:]))
            if len(trimmed) >= 3:
                segments.append((trimmed[-1], trimmed[0]))
    return segments


def _build_segment_solids(
    bead: Bead,
    profile: list[tuple[float, float]],
) -> list[PlanarSolid]:
    """Fallback for sharp reversals: one prism per segment, as before."""

    solids: list[PlanarSolid] = []
    for start, end in _segments(bead):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        nx, ny = -dy / length, dx / length
        count = len(profile)
        ring_start = [
            (start[0] + lateral * nx, start[1] + lateral * ny, start[2] + vertical)
            for lateral, vertical in profile
        ]
        ring_end = [
            (end[0] + lateral * nx, end[1] + lateral * ny, end[2] + vertical)
            for lateral, vertical in profile
        ]
        points = ring_start + ring_end
        faces: list[list[int]] = [list(range(count)), list(range(count, 2 * count))]
        for index in range(count):
            following = (index + 1) % count
            faces.append(
                [index, following, count + following, count + index]
            )
        _orient_faces_consistently(points, faces)
        faces = _planarize(points, faces)
        solids.append(PlanarSolid(points=points, faces=faces))
    return solids


def _header(name: str) -> list[str]:
    return [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('GCode-FEA bead mesostructure'),'2;1');",
        (
            "FILE_NAME('"
            + _escape(name)
            + ".step','1970-01-01T00:00:00',(''),(''),'gcode-fea','gcode-fea','');"
        ),
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
        "ENDSEC;",
        "DATA;",
        "#1 = APPLICATION_CONTEXT('core data for automotive mechanical design processes');",
        (
            "#2 = APPLICATION_PROTOCOL_DEFINITION('international standard',"
            "'automotive_design',2000,#1);"
        ),
        "#3 = PRODUCT_CONTEXT('',#1,'mechanical');",
        "#4 = PRODUCT('GCodeFEA','GCodeFEA','',(#3));",
        "#5 = PRODUCT_DEFINITION_FORMATION('','',#4);",
        "#6 = PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design');",
        "#7 = PRODUCT_DEFINITION('design','',#5,#6);",
        "#8 = PRODUCT_DEFINITION_SHAPE('','',#7);",
        f"#{_SHAPE_REP_ID} = SHAPE_DEFINITION_REPRESENTATION(#8,#20);",
        "#9 = (LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));",
        "#10 = (NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));",
        "#11 = (NAMED_UNIT(*) SOLID_ANGLE_UNIT() SI_UNIT($,.STERADIAN.));",
        (
            "#12 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#9,"
            "'distance_accuracy_value','');"
        ),
        (
            "#13 = (GEOMETRIC_REPRESENTATION_CONTEXT(3) "
            "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#12)) "
            "GLOBAL_UNIT_ASSIGNED_CONTEXT((#9,#10,#11)) "
            "REPRESENTATION_CONTEXT('',''));"
        ),
        "#14 = CARTESIAN_POINT('',(0.,0.,0.));",
        "#15 = DIRECTION('',(0.,0.,1.));",
        "#16 = DIRECTION('',(1.,0.,0.));",
        "#17 = AXIS2_PLACEMENT_3D('',#14,#15,#16);",
    ]


def _patch_representation(lines: list[str], solid_ids: list[int]) -> None:
    refs = ", ".join(f"#{entity}" for entity in [17, *solid_ids])
    lines[15] = (
        f"#{_SHAPE_REP_ID} = SHAPE_DEFINITION_REPRESENTATION(#8,#{_SHAPE_REP_ID + 1});"
    )
    lines.append(
        f"#{_SHAPE_REP_ID + 1} = ADVANCED_BREP_SHAPE_REPRESENTATION('',({refs}),#13);"
    )


def _planarize(points: list[Point3], faces: list[list[int]]) -> list[list[int]]:
    """Split non-planar polygons into triangles.

    ADVANCED_FACE with a PLANE surface requires a planar loop. Straight-section
    quads are planar; the quads that span a mitred corner are usually not.
    """

    result: list[list[int]] = []
    for face in faces:
        if len(face) <= 3 or _is_planar(points, face):
            result.append(face)
            continue
        for index in range(1, len(face) - 1):
            result.append([face[0], face[index], face[index + 1]])
    return result


def _is_planar(points: list[Point3], face: list[int]) -> bool:
    normal = _newell(points, face)
    if normal is None:
        return False
    unit = _unit(normal)
    if unit is None:
        return False
    origin = points[face[0]]
    for index in face[1:]:
        distance = abs(_dot(unit, _sub(points[index], origin)))
        if distance > 1e-7:
            return False
    return True


def _orient_faces_consistently(points: list[Point3], faces: list[list[int]]) -> None:
    """Orient a closed shell by edge-adjacency, not by a centroid.

    A bent bead is concave on the inside of the turn, so a centroid test flips
    the inner faces. Propagating orientation across shared edges works for any
    orientable closed shell.
    """

    if not faces:
        return
    edge_faces: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for face_index, face in enumerate(faces):
        count = len(face)
        for index in range(count):
            u = face[index]
            v = face[(index + 1) % count]
            key = (u, v) if u < v else (v, u)
            direction = 1 if u == key[0] else -1
            edge_faces.setdefault(key, []).append((face_index, direction))

    flips: dict[int, bool] = {}
    for start in range(len(faces)):
        if start in flips:
            continue
        flips[start] = False
        stack = [start]
        while stack:
            current = stack.pop()
            face = faces[current]
            count = len(face)
            for index in range(count):
                u = face[index]
                v = face[(index + 1) % count]
                key = (u, v) if u < v else (v, u)
                current_direction = 1 if u == key[0] else -1
                if flips[current]:
                    current_direction = -current_direction
                for neighbour, neighbour_direction in edge_faces.get(key, []):
                    if neighbour == current:
                        continue
                    want_flip = neighbour_direction == current_direction
                    if neighbour not in flips:
                        flips[neighbour] = want_flip
                        stack.append(neighbour)
    for face_index, face in enumerate(faces):
        if flips.get(face_index):
            face.reverse()
    if _volume(points, faces) < 0:
        for face in faces:
            face.reverse()


def _volume(points: list[Point3], faces: list[list[int]]) -> float:
    total = 0.0
    for face in faces:
        origin = points[face[0]]
        for index in range(1, len(face) - 1):
            a = points[face[index]]
            b = points[face[index + 1]]
            total += _dot(origin, _cross(a, b)) / 6.0
    return total


def _orient_face(points: list[Point3], face: list[int], centroid: Point3) -> None:
    normal = _newell(points, face)
    if normal is None:
        return
    center = (
        sum(points[index][0] for index in face) / len(face),
        sum(points[index][1] for index in face) / len(face),
        sum(points[index][2] for index in face) / len(face),
    )
    if _dot(normal, _sub(center, centroid)) < 0:
        face.reverse()


def _newell(points: list[Point3], face: list[int]) -> Point3 | None:
    nx = ny = nz = 0.0
    count = len(face)
    for index in range(count):
        current = points[face[index]]
        following = points[face[(index + 1) % count]]
        nx += (current[1] - following[1]) * (current[2] + following[2])
        ny += (current[2] - following[2]) * (current[0] + following[0])
        nz += (current[0] - following[0]) * (current[1] + following[1])
    normal = (nx, ny, nz)
    if math.sqrt(_dot(normal, normal)) <= 1e-15:
        return None
    return normal


def _reference_direction(
    points: list[Point3],
    face: list[int],
    normal: Point3,
) -> Point3:
    for index in range(len(face)):
        edge = _sub(points[face[(index + 1) % len(face)]], points[face[index]])
        projected = _sub(edge, _scale(normal, _dot(edge, normal)))
        unit = _unit(projected)
        if unit is not None:
            return unit
    return (1.0, 0.0, 0.0)


def _closed(points: list[Point3]) -> bool:
    if len(points) < 4:
        return False
    return math.dist(points[0], points[-1]) <= 1e-9


def _vertex_tangents(points: list[Point3], closed: bool) -> list[Point3]:
    count = len(points)
    tangents: list[Point3] = []
    for index in range(count):
        if closed:
            previous = points[(index - 1) % count]
            current = points[index]
            following = points[(index + 1) % count]
            incoming = _unit(_sub(current, previous))
            outgoing = _unit(_sub(following, current))
            tangent = _unit(_add(incoming, outgoing)) if incoming and outgoing else None
        elif index == 0:
            tangent = _unit(_sub(points[1], points[0]))
        elif index == count - 1:
            tangent = _unit(_sub(points[-1], points[-2]))
        else:
            incoming = _unit(_sub(points[index], points[index - 1]))
            outgoing = _unit(_sub(points[index + 1], points[index]))
            tangent = _unit(_add(incoming, outgoing)) if incoming and outgoing else None
        if tangent is None:
            tangent = _unit(_sub(points[(index + 1) % count], points[index]))
        if tangent is None:
            tangent = (1.0, 0.0, 0.0)
        tangents.append(tangent)
    return tangents


def _miter_scales(
    points: list[Point3],
    tangents: list[Point3],
    closed: bool,
) -> list[float] | None:
    """Lateral miter scale per vertex, or None for a degenerate corner.

    The cross-section in the bisecting plane is wider by ``1 / cos(theta/2)``.
    Very sharp reversals (raster zigzags) are not mitred; those beads keep
    their segment prisms so the joint does not become unbounded.
    """

    count = len(points)
    scales: list[float] = []
    for index in range(count):
        if closed:
            previous = points[(index - 1) % count]
            following = points[(index + 1) % count]
        elif index == 0:
            previous, following = points[0], points[1]
        elif index == count - 1:
            previous, following = points[-2], points[-1]
        else:
            previous, following = points[index - 1], points[index + 1]
        incoming = _unit(_sub(points[index], previous))
        outgoing = _unit(_sub(following, points[index]))
        if incoming is None or outgoing is None:
            scales.append(1.0)
            continue
        cosine = _dot(incoming, outgoing)
        cos_half = math.sqrt(max(0.0, 0.5 * (1.0 + cosine)))
        if cos_half <= 0.25:
            return None
        scales.append(min(4.0, 1.0 / cos_half))
    return scales


def _ring_points(
    center: Point3,
    tangent: Point3,
    profile: list[tuple[float, float]],
    lateral_scale: float = 1.0,
) -> list[Point3] | None:
    tx, ty = tangent[0], tangent[1]
    norm = math.hypot(tx, ty)
    if norm <= 1e-12:
        tx, ty, norm = 1.0, 0.0, 1.0
    nx, ny = -ty / norm, tx / norm
    return [
        (
            center[0] + lateral * lateral_scale * nx,
            center[1] + lateral * lateral_scale * ny,
            center[2] + vertical,
        )
        for lateral, vertical in profile
    ]


def _rdp(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        best = -1.0
        index = -1
        for candidate in range(first + 1, last):
            distance = _point_segment_distance(
                points[candidate], points[first], points[last]
            )
            if distance > best:
                best = distance
                index = candidate
        if best > tolerance and index >= 0:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [point for point, selected in zip(points, keep) if selected]


def _point_segment_distance(point, start, end) -> float:
    ax, ay = start
    bx, by = end
    px, py = point
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _distance2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _add(a: Point3, b: Point3) -> Point3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _refs(ids: list[int]) -> str:
    return ",".join(f"#{entity}" for entity in ids)


def _num(value: float) -> str:
    text = f"{value:.10g}"
    if "e" not in text and "E" not in text and "." not in text:
        text += "."
    return text


def _escape(value: str) -> str:
    return value.replace("'", "''")


def _unit(vector: Point3) -> Point3 | None:
    norm = math.sqrt(_dot(vector, vector))
    if norm <= 1e-12:
        return None
    return (vector[0] / norm, vector[1] / norm, vector[2] / norm)


def _scale(vector: Point3, factor: float) -> Point3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _sub(a: Point3, b: Point3) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Point3, b: Point3) -> Point3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
