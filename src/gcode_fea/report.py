"""Sidecar and report generation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .contacts import contact_stats
from .model import Contact, SliceModel

SIDECAR_SCHEMA = "gcode_fea.sidecar.v1"
REPORT_SCHEMA = "gcode_fea.report.v1"


def build_sidecar(
    model: SliceModel,
    contacts: list[Contact],
    mesh_summary: dict | None = None,
    interfaces: list | None = None,
    bead_process: dict | None = None,
    process_summary: dict | None = None,
    adhesion: dict | None = None,
) -> dict:
    beads = []
    for bead in model.sorted_beads():
        beads.append(
            {
                "id": bead.bead_id,
                "object_id": bead.object_id,
                "layer_index": bead.layer_index,
                "print_z": bead.print_z,
                "role": bead.role,
                "material": bead.material,
                "points": [list(point) for point in bead.points],
                "nominal_width": bead.nominal_width,
                "height": bead.height,
                "area_per_length": bead.area_per_length,
                "length": bead.length,
                "volume": bead.volume,
                "bbox": list(bead.bbox()),
                "cross_section": {
                    "mode": bead.cross_section.mode,
                    "height": bead.cross_section.height,
                    "area": bead.cross_section.area,
                    "width_flat": bead.cross_section.width_flat,
                    "width_total": bead.cross_section.width_total,
                    "flat_side_height": bead.cross_section.flat_side_height,
                    "sagitta": bead.cross_section.sagitta,
                    "radius": bead.cross_section.radius,
                    "flat_top_fraction": bead.cross_section.flat_top_fraction,
                    "flat_side_fraction": bead.cross_section.flat_side_fraction,
                    "profile": [list(point) for point in bead.cross_section.profile],
                    "warnings": list(bead.cross_section.warnings),
                },
            }
        )
    contacts_payload = [
        {
            "a": contact.id_a,
            "b": contact.id_b,
            "kind": contact.kind,
            "area": contact.area,
            "normal": list(contact.normal),
            "centroid": list(contact.centroid),
            "overlap": contact.overlap,
            "gap": contact.gap,
            "resolution": contact.resolution,
        }
        for contact in contacts
    ]
    payload = {
        "schema": SIDECAR_SCHEMA,
        "meta": dict(model.meta),
        "beads": beads,
        "contacts": contacts_payload,
        "mesh_summary": mesh_summary or {},
    }
    if interfaces is not None:
        payload["interfaces"] = [
            {
                **item.to_dict(),
                "bond_quality": item.bond_quality,
            }
            for item in interfaces
        ]
    if bead_process is not None:
        payload["process"] = {
            "beads": [
                record.as_dict() for record in bead_process.values()
            ],
            "summary": process_summary or {},
        }
    if adhesion is not None:
        payload["adhesion"] = adhesion
    return payload


def build_report(
    model: SliceModel,
    contacts: list[Contact],
    resolution: float,
    mesh_summary: dict | None = None,
    roi: tuple[float, float, float, float, float, float] | None = None,
    warnings: list[str] | None = None,
    interfaces: list | None = None,
    adhesion: dict | None = None,
) -> dict:
    min_width = min(bead.cross_section.width_total for bead in model.beads) if model.beads else None
    stats = contact_stats(contacts, resolution, min_width)
    role_counts = Counter(bead.role for bead in model.beads)
    material_counts = Counter(bead.material for bead in model.beads)
    layer_count = len(model.layers())
    analytic_volume = model.volume
    mesh_volume = mesh_summary.get("mesh_volume") if mesh_summary else None
    watertight_fraction = mesh_summary.get("watertight_fraction") if mesh_summary else None
    volume_error = None
    if mesh_volume is not None and analytic_volume > 0:
        volume_error = abs(mesh_volume - analytic_volume) / analytic_volume
    all_warnings = list(warnings or [])
    all_warnings.extend(stats.get("warnings", []))
    if adhesion is not None:
        all_warnings.extend(adhesion.get("warnings", []))
    for bead in model.beads:
        all_warnings.extend(f"{bead.bead_id}: {warning}" for warning in bead.cross_section.warnings)
    payload = {
        "schema": REPORT_SCHEMA,
        "source": model.meta.get("source", "unknown"),
        "meta": {
            "orcaslicer_version": model.meta.get("orcaslicer_version", "unknown"),
            "mode": model.meta.get("mode", "unknown"),
            "c_h": model.meta.get("c_h"),
            "c_v": model.meta.get("c_v"),
            "units": model.meta.get("units", "mm"),
            "frame": model.meta.get("frame", "printer"),
            "roi": list(roi) if roi is not None else None,
        },
        "summary": {
            "bead_count": len(model.beads),
            "layer_count": layer_count,
            "role_counts": dict(sorted(role_counts.items())),
            "material_counts": dict(sorted(material_counts.items())),
            "analytic_volume": analytic_volume,
            "mesh_volume": mesh_volume,
            "volume_relative_error": volume_error,
            "watertight_fraction": watertight_fraction,
            "bbox": list(model.bbox()),
            "contact_count": stats["count"],
            "intralayer_count": stats["intralayer_count"],
            "interlayer_count": stats["interlayer_count"],
            "intralayer_area": stats["intralayer_area"],
            "interlayer_area": stats["interlayer_area"],
            "contact_resolution": resolution,
            "interface_count": len(interfaces) if interfaces is not None else None,
            "mean_bond_quality": (
                sum(item.bond_quality for item in interfaces) / len(interfaces)
                if interfaces
                else None
            ),
            "min_bond_quality": (
                min(item.bond_quality for item in interfaces)
                if interfaces
                else None
            ),
        },
        "warnings": sorted(set(all_warnings)),
    }
    if adhesion is not None:
        payload["adhesion"] = {
            key: value
            for key, value in adhesion.items()
            if key != "details"
        }
    payload["content_hash"] = content_hash(payload)
    return payload


def content_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_sidecar(
    model: SliceModel,
    contacts: list[Contact],
    path: str | Path,
    mesh_summary: dict | None = None,
    interfaces: list | None = None,
    bead_process: dict | None = None,
    process_summary: dict | None = None,
    adhesion: dict | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            build_sidecar(
                model,
                contacts,
                mesh_summary,
                interfaces,
                bead_process,
                process_summary,
                adhesion,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return target


def write_report(
    model: SliceModel,
    contacts: list[Contact],
    path: str | Path,
    resolution: float,
    mesh_summary: dict | None = None,
    roi: tuple[float, float, float, float, float, float] | None = None,
    warnings: list[str] | None = None,
    interfaces: list | None = None,
    adhesion: dict | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            build_report(
                model,
                contacts,
                resolution,
                mesh_summary,
                roi,
                warnings,
                interfaces,
                adhesion,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return target
