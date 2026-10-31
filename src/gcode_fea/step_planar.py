"""Pure-Python planar B-rep STEP export for bead compounds.

gmsh/OCCT gives the best STEP, but gmsh is GPL and is not bundled with the
plugin. This module writes a standard AP214 ``MANIFOLD_SOLID_BREP`` using only
the standard library: one closed solid per straight segment prism, with planar
``ADVANCED_FACE`` surfaces.

Each segment prism is a closed convex polyhedron (two profile caps plus one
quad per profile edge), so every solid has a valid closed shell. The output is
flat-faceted rather than an analytic swept surface; use ``profile_stride`` and
an ROI to keep the file small. OCCT, FreeCAD, PrePoMax, and other STEP viewers
read this form.
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
class PlanarPrism:
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


def build_prisms(bead: Bead, profile_stride: int = 1) -> list[PlanarPrism]:
    profile = list(bead.cross_section.profile)
    if profile_stride > 1 and len(profile) >= 16:
        profile = profile[::profile_stride]
    if len(profile) < 3:
        return []
    prisms: list[PlanarPrism] = []
    for start, end in _segments(bead):
        prism = _prism(start, end, profile)
        if prism is not None:
            prisms.append(prism)
    return prisms


def estimate_faces(model: SliceModel, profile_stride: int = 1) -> int:
    """Preflight estimate of the planar STEP face count.

    Each segment prism contributes two caps plus one quad per profile point.
    The estimate is exact for the default segment-prism sweep and is used by the
    plugin to refuse an export that would fill the disk.
    """

    faces = 0
    for bead in model.beads:
        profile = list(bead.cross_section.profile)
        if profile_stride > 1 and len(profile) >= 16:
            profile = profile[::profile_stride]
        count = len(profile)
        if count < 3:
            continue
        segments = len(_segments(bead))
        faces += segments * (count + 2)
    return faces


def write_step_planar(
    model: SliceModel,
    path: str | Path,
    profile_stride: int = 1,
    name: str = "gcode_fea",
) -> dict:
    """Write bead segment prisms as AP214 solids with planar faces."""

    if not model.beads:
        raise ValueError("cannot export an empty model")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    solids: list[PlanarPrism] = []
    for bead in model.sorted_beads():
        solids.extend(build_prisms(bead, profile_stride))
    if not solids:
        raise ValueError("no bead segments could be converted to solids")

    lines = _header(name)
    next_id = _CONTEXT_ENTITY_COUNT + 1
    solid_ids: list[int] = []
    face_count = 0
    point_count = 0
    for prism in solids:
        solid_id, next_id = _emit_solid(lines, prism, next_id)
        solid_ids.append(solid_id)
        face_count += len(prism.faces)
        point_count += len(prism.points)

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
    }


def _emit_solid(
    lines: list[str],
    prism: PlanarPrism,
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


def _prism(
    start: Point3,
    end: Point3,
    profile: list[tuple[float, float]],
) -> PlanarPrism | None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-12:
        return None
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
        faces.append([index, following, count + following, count + index])

    centroid = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
        sum(point[2] for point in points) / len(points),
    )
    for face in faces:
        _orient_face(points, face, centroid)
    return PlanarPrism(points=points, faces=faces)


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
