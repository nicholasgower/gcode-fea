"""STL and STEP export."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np

from .model import Bead, SliceModel
from .sweep import Mesh, _normalize, _ring, mesh_bead, mesh_model


def write_stl(mesh: Mesh, path: str | Path) -> Path:
    """Write a binary STL. The mesh may be a compound of bead solids."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if mesh.n_faces == 0:
        raise ValueError("cannot write an empty mesh")
    v = mesh.vertices
    f = mesh.faces
    tri = v[f]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths <= 1e-18] = 1.0
    normals = normals / lengths[:, None]
    with target.open("wb") as handle:
        handle.write(b"GCode-FEA binary STL".ljust(80, b" "))
        handle.write(struct.pack("<I", mesh.n_faces))
        for normal, triangle in zip(normals, tri):
            handle.write(struct.pack("<3f", *normal.astype(float)))
            for vertex in triangle:
                handle.write(struct.pack("<3f", *vertex.astype(float)))
            handle.write(struct.pack("<H", 0))
    return target


def write_stl_model(model: SliceModel, path: str | Path, join: str = "segments") -> Path:
    return write_stl(mesh_model(model, join), path)


def export_step(
    model: SliceModel,
    path: str | Path,
    verify: bool = True,
    profile_stride: int = 1,
    step_engine: str = "auto",
) -> dict:
    """Export each bead as a compound of straight-segment prisms.

    Engines:

    - ``gmsh``: OCCT B-rep behind gmsh (optional GPL dependency),
    - ``planar``: pure-Python AP214 solids with planar faces (no extra deps),
    - ``auto``: gmsh when importable, otherwise planar.

    Both engines keep the compound as one solid per segment prism so the volume
    sum remains explicit.
    """

    if step_engine not in ("auto", "gmsh", "planar"):
        raise ValueError("step_engine must be 'auto', 'gmsh', or 'planar'")
    target = Path(path)
    if step_engine == "planar":
        return _export_step_planar(model, target, profile_stride)
    try:
        import gmsh
    except ImportError:
        if step_engine == "gmsh":
            raise RuntimeError(
                "gmsh is required for the gmsh STEP engine; use "
                "`--step-engine planar` or `uv run --with gmsh ...`"
            )
        return _export_step_planar(model, target, profile_stride)

    target.parent.mkdir(parents=True, exist_ok=True)
    if not model.beads:
        raise ValueError("cannot export an empty model")

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("gcode_fea")
    volumes: list[tuple[int, int]] = []
    segment_count = 0
    for bead in model.sorted_beads():
        profile = np.asarray(bead.cross_section.profile, dtype=float)
        if profile_stride > 1 and len(profile) >= 16:
            profile = profile[::profile_stride]
        for start, end in _segments(bead):
            p0 = np.asarray(start, dtype=float)
            p1 = np.asarray(end, dtype=float)
            direction = p1 - p0
            if float(np.linalg.norm(direction)) <= 1e-12:
                continue
            tangent = _normalize(direction)
            ring = _ring(p0, tangent, profile)
            points = [gmsh.model.occ.addPoint(float(x), float(y), float(z)) for x, y, z in ring]
            lines = [
                gmsh.model.occ.addLine(points[i], points[(i + 1) % len(points)])
                for i in range(len(points))
            ]
            loop = gmsh.model.occ.addCurveLoop(lines)
            surface = gmsh.model.occ.addPlaneSurface([loop])
            prism = gmsh.model.occ.extrude(
                [(2, surface)],
                float(direction[0]),
                float(direction[1]),
                float(direction[2]),
            )
            volumes.extend(prism)
            segment_count += 1
    gmsh.model.occ.synchronize()
    gmsh.write(str(target))
    text = target.read_text()
    target.write_text("\n".join(line.rstrip() for line in text.splitlines()) + "\n")
    gmsh.finalize()

    result = {
        "path": str(target),
        "engine": "gmsh",
        "segment_prisms": segment_count,
        "volumes": len(volumes),
    }
    if verify:
        result.update(_verify_step(target, model.volume))
    return result


def _export_step_planar(
    model: SliceModel,
    target: Path,
    profile_stride: int,
) -> dict:
    from .step_planar import write_step_planar

    result = write_step_planar(
        model,
        target,
        profile_stride=profile_stride,
    )
    result["segment_prisms"] = result["solids"]
    result["bbox"] = list(model.bbox())
    return result


def _verify_step(path: Path, expected_volume: float) -> dict:
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("verify")
    gmsh.open(str(path))
    solids = gmsh.model.getEntities(3)
    volume = 0.0
    for dim, tag in solids:
        volume += gmsh.model.occ.getMass(dim, tag)
    bbox = tuple(gmsh.model.getBoundingBox(-1, -1))
    gmsh.finalize()
    error = abs(volume - expected_volume) / expected_volume if expected_volume > 0 else None
    return {
        "solid_count": len(solids),
        "volume": volume,
        "volume_relative_error": error,
        "bbox": bbox,
    }


def _segments(bead: Bead) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    points = list(bead.points)
    if len(points) < 2:
        return []
    segments = list(zip(points, points[1:]))
    if len(points) >= 4:
        a, b = points[0], points[-1]
        if math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) <= 1e-9:
            points = points[:-1]
            segments = list(zip(points, points[1:]))
            if len(points) >= 3:
                segments.append((points[-1], points[0]))
    return segments
