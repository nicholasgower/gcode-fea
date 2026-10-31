"""Slice-cache schema and Phase 0 probe conversion."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .cross_section import solve_cross_section
from .model import Bead, CrossSection, SliceModel

SCHEMA = "gcode_fea.slice_cache.v1"


def load_probe(
    path: str | Path,
    mode: str = "volume_conserving",
    c_h: float = 0.7,
    c_v: float = 0.225,
    nominal_width: float | None = None,
) -> SliceModel:
    """Convert a Phase 0/Phase 1 probe JSON file into a SliceModel."""

    source = Path(path)
    data = json.loads(source.read_text())
    if "layers" in data:
        layer_payloads = [
            (int(layer.get("layer_index", index)), layer)
            for index, layer in enumerate(data["layers"])
        ]
        truncated = bool(data.get("truncated", False))
    elif "paths" in data:
        layer_payloads = [(int(data.get("layer_index", 0)), data)]
        truncated = False
    else:
        raise ValueError(f"{source} is not a Phase 0/Phase 1 probe JSON")

    object_id = int(data.get("object_id", 0))
    beads: list[Bead] = []
    for layer_index, layer_data in layer_payloads:
        print_z = float(
            layer_data.get("print_z", layer_data.get("layer_print_z", 0.0))
        )
        for path_index, path_data in enumerate(layer_data.get("paths", [])):
            height = float(path_data["height"])
            area = float(path_data["mm3_per_mm"])
            width = float(path_data["width"])
            if nominal_width is not None:
                width = float(nominal_width)
            points = _probe_points(path_data["points_mm"], print_z, height)
            if len(points) < 2:
                continue
            cross_section = solve_cross_section(
                area=area,
                height=height,
                mode=mode,
                nominal_width=width,
                c_h=c_h,
                c_v=c_v,
            )
            bead_id = f"obj{object_id}_L{layer_index:04d}_P{path_index:05d}"
            beads.append(
                Bead(
                    bead_id=bead_id,
                    object_id=object_id,
                    layer_index=layer_index,
                    print_z=print_z,
                    role=str(path_data.get("role", "unknown")),
                    points=points,
                    nominal_width=width,
                    height=height,
                    area_per_length=area,
                    cross_section=cross_section,
                    material=str(path_data.get("material", "default")),
                    path_index=path_index,
                    source=source.name,
                )
            )

    first_layer = layer_payloads[0][1] if layer_payloads else {}
    meta = {
        "schema": SCHEMA,
        "source": source.name,
        "orcaslicer_version": data.get("orcaslicer_version", "unknown"),
        "mode": mode,
        "c_h": float(c_h),
        "c_v": float(c_v),
        "units": "mm",
        "frame": "printer",
        "layer_count": len(layer_payloads),
        "truncated": truncated,
        "layer_height": float(
            first_layer.get("height", first_layer.get("layer_height", 0.0))
        ),
    }
    if not beads:
        raise ValueError(f"{source} contains no usable extrusion paths")
    return SliceModel(beads=beads, meta=meta)


def save_cache(model: SliceModel, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "meta": model.meta,
        "beads": [_bead_to_dict(bead) for bead in model.sorted_beads()],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def load_cache(path: str | Path) -> SliceModel:
    source = Path(path)
    payload = json.loads(source.read_text())
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"{source} has unsupported schema: {payload.get('schema')}")
    beads = [_bead_from_dict(item) for item in payload.get("beads", [])]
    if not beads:
        raise ValueError(f"{source} contains no beads")
    return SliceModel(beads=beads, meta=dict(payload.get("meta", {})))


def _probe_points(
    points: list[list[float]],
    print_z: float,
    height: float,
) -> tuple[tuple[float, float, float], ...]:
    if not points:
        return ()
    zs = [float(point[2]) for point in points]
    if max(zs) - min(zs) <= 1e-9 and abs(zs[0]) <= 1e-9:
        bottom = print_z - height
        return tuple((float(p[0]), float(p[1]), bottom) for p in points)
    return tuple((float(p[0]), float(p[1]), float(p[2])) for p in points)


def _bead_to_dict(bead: Bead) -> dict:
    cs = bead.cross_section
    return {
        "id": bead.bead_id,
        "object_id": bead.object_id,
        "layer_index": bead.layer_index,
        "print_z": bead.print_z,
        "role": bead.role,
        "points": [list(point) for point in bead.points],
        "nominal_width": bead.nominal_width,
        "height": bead.height,
        "area_per_length": bead.area_per_length,
        "material": bead.material,
        "path_index": bead.path_index,
        "source": bead.source,
        "cross_section": {
            "mode": cs.mode,
            "height": cs.height,
            "area": cs.area,
            "width_flat": cs.width_flat,
            "width_total": cs.width_total,
            "flat_side_height": cs.flat_side_height,
            "sagitta": cs.sagitta,
            "radius": cs.radius if cs.radius is not None and math.isfinite(cs.radius) else None,
            "profile": [list(point) for point in cs.profile],
            "warnings": list(cs.warnings),
        },
    }


def _bead_from_dict(item: dict) -> Bead:
    cs_data = item["cross_section"]
    cross_section = CrossSection(
        mode=cs_data["mode"],
        height=float(cs_data["height"]),
        area=float(cs_data["area"]),
        width_flat=float(cs_data["width_flat"]),
        width_total=float(cs_data["width_total"]),
        flat_side_height=float(cs_data["flat_side_height"]),
        sagitta=float(cs_data["sagitta"]),
        radius=None if cs_data.get("radius") is None else float(cs_data["radius"]),
        profile=tuple((float(p[0]), float(p[1])) for p in cs_data["profile"]),
        warnings=tuple(str(w) for w in cs_data.get("warnings", [])),
    )
    return Bead(
        bead_id=str(item["id"]),
        object_id=int(item["object_id"]),
        layer_index=int(item["layer_index"]),
        print_z=float(item["print_z"]),
        role=str(item["role"]),
        points=tuple((float(p[0]), float(p[1]), float(p[2])) for p in item["points"]),
        nominal_width=float(item["nominal_width"]),
        height=float(item["height"]),
        area_per_length=float(item["area_per_length"]),
        cross_section=cross_section,
        material=str(item.get("material", "default")),
        path_index=int(item.get("path_index", 0)),
        source=str(item.get("source", "")),
    )
