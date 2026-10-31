"""Interface extraction from contacts and voxel meshes.

Phase 2 used a geometric contact proxy: intralayer/interlayer contact area from
a 2.5D footprint grid. Phase 3 turns those contacts into explicit interface
objects that can carry a cohesive card, a solver surface, and (Phase 4) a
spatially varying bond quality.

Areas always come from the contact model. When a voxel mesh is supplied, the
interface also carries the mesh faces that realize it, and the face area is
scaled down or up to the contact-model area. That scaling matters because a
voxel staircase overestimates the octagon contact height until ``c_v * h`` is
resolved.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .contacts import detect_contacts
from .model import SliceModel
from .voxel import InterfacePatch, VoxelMesh, interface_patches


@dataclass
class Interface:
    """One bead-pair interface."""

    interface_id: str
    id_a: str
    id_b: str
    kind: str
    area: float
    normal: tuple[float, float, float]
    centroid: tuple[float, float, float]
    faces: tuple[tuple[int, int, int, int], ...] = ()
    raw_area: float = 0.0
    area_scale: float = 1.0
    bond_quality: float = 1.0
    area_model: str = "contact_proxy"

    @property
    def face_count(self) -> int:
        return len(self.faces)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["faces"] = [list(face) for face in self.faces]
        payload["face_count"] = self.face_count
        return payload


def interface_id(id_a: str, id_b: str) -> str:
    first, second = sorted((id_a, id_b))
    return f"iface_{first}__{second}"


def extract_interfaces(
    model: SliceModel,
    mesh: VoxelMesh | None = None,
    resolution: float = 0.1,
) -> list[Interface]:
    """Build interface objects from the contact model, optionally with faces."""

    contacts = detect_contacts(model, resolution=resolution)
    beads = model.by_id()
    patches: dict[tuple[str, str], InterfacePatch] = {}
    if mesh is not None:
        for patch in interface_patches(mesh):
            patches[tuple(sorted((patch.id_a, patch.id_b)))] = patch

    interfaces: list[Interface] = []
    for contact in contacts:
        key = tuple(sorted((contact.id_a, contact.id_b)))
        patch = patches.get(key)
        area, area_model = _contact_area(contact, beads)
        faces: tuple[tuple[int, int, int, int], ...] = ()
        raw_area = 0.0
        scale = 1.0
        if patch is not None:
            faces = tuple(patch.faces)
            raw_area = patch.area
            if raw_area > 0:
                scale = area / raw_area
        interfaces.append(
            Interface(
                interface_id=interface_id(contact.id_a, contact.id_b),
                id_a=contact.id_a,
                id_b=contact.id_b,
                kind=contact.kind,
                area=area,
                normal=tuple(contact.normal),
                centroid=tuple(contact.centroid),
                faces=faces,
                raw_area=raw_area,
                area_scale=scale,
                area_model=area_model,
            )
        )

    interfaces.sort(key=lambda item: (item.kind, item.id_a, item.id_b))
    return interfaces


def _contact_area(
    contact: object,
    beads: dict[str, object],
) -> tuple[float, str]:
    """Physical interface area for a contact record.

    Interlayer contact area already includes the flat-top fraction. Intralayer
    contact is reported by the 2.5D grid as boundary length times bead height;
    scale it by the flat-side fraction when the cross-section provides one.
    Barrel cross-sections have no flat side, so the height proxy is kept and
    flagged through ``area_model``.
    """

    kind = getattr(contact, "kind")
    area = float(getattr(contact, "area"))
    if kind != "intralayer":
        return area, "contact_proxy"
    bead_a = beads.get(getattr(contact, "id_a"))
    bead_b = beads.get(getattr(contact, "id_b"))
    if bead_a is None or bead_b is None:
        return area, "contact_proxy"
    fraction = min(
        bead_a.cross_section.flat_side_fraction,
        bead_b.cross_section.flat_side_fraction,
    )
    if fraction <= 0.0:
        return area, "contact_proxy"
    return area * fraction, "flat_side_scaled"


def interface_report(
    interfaces: list[Interface],
    tolerance: float = 0.1,
) -> dict:
    intralayer = [item for item in interfaces if item.kind == "intralayer"]
    interlayer = [item for item in interfaces if item.kind == "interlayer"]
    scales = [item.area_scale for item in interfaces if item.raw_area > 0]
    warnings: list[str] = []
    if scales and (max(scales) > 1.0 + tolerance or min(scales) < 1.0 - tolerance):
        warnings.append(
            "voxel face area differs from the contact area by more than "
            f"{tolerance:.0%}; interface stiffness uses the scaled area"
        )
    missing = sum(1 for item in interfaces if item.raw_area == 0)
    if missing:
        warnings.append(f"{missing} interface(s) have no mesh faces")
    return {
        "count": len(interfaces),
        "intralayer_count": len(intralayer),
        "interlayer_count": len(interlayer),
        "intralayer_area": sum(item.area for item in intralayer),
        "interlayer_area": sum(item.area for item in interlayer),
        "total_area": sum(item.area for item in interfaces),
        "raw_face_area": sum(item.raw_area for item in interfaces),
        "mean_area_scale": (sum(scales) / len(scales)) if scales else None,
        "warnings": warnings,
    }


def interface_sets(interfaces: list[Interface]) -> dict[str, list[str]]:
    """Group interface IDs by kind, layer pair, and bead pair."""

    grouped: dict[str, list[str]] = {}
    for item in interfaces:
        grouped.setdefault(item.kind, []).append(item.interface_id)
    return grouped


def interface_area_by_pair(
    interfaces: list[Interface],
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for item in interfaces:
        key = tuple(sorted((item.id_a, item.id_b)))  # type: ignore[assignment]
        result[key] = result.get(key, 0.0) + item.area
    return result


def projected_normal(normal: tuple[float, float, float]) -> tuple[float, float, float]:
    norm = math.sqrt(sum(component * component for component in normal))
    if norm <= 1e-12:
        return (0.0, 0.0, 0.0)
    return tuple(component / norm for component in normal)  # type: ignore[return-value]
