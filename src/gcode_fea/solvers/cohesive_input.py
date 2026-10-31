"""Cohesive interface meshing and Abaqus cohesive input writing.

Solid elements are cut apart at bead interfaces: elements are grouped into
connected components using shared faces, nodes on an interface are duplicated
per component, and each interface face becomes a zero-thickness COH3D8 cohesive
element. This is the standard Abaqus zero-thickness cohesive construction; the
cohesive constitutive thickness is set to 1.0 so separations are strain-like.

Limitations: a node shared by more than two beads receives one copy per
component and one cohesive element per bead pair, which can create very small
junctions. Abaqus is the only end-to-end target here; CalculiX cohesive input is
not generated yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..cohesive import CohesiveCard
from ..interfaces import Interface, extract_interfaces, interface_report
from ..voxel import HEX_FACES, VoxelMesh
from .coupon import coupon_node_sets
from .inp import _chunked_set, _write

Array = np.ndarray


@dataclass
class SplitMesh:
    nodes: Array
    elements: Array
    cohesive: Array
    element_bead: Array
    cohesive_area: Array
    cohesive_kind: tuple[str, ...]
    bead_ids: tuple[str, ...]
    resolution: float
    origin: tuple[float, float, float]
    element_layer: Array
    element_role: Array
    interface_ids: tuple[str, ...] = ()
    meta: dict = field(default_factory=dict)

    @property
    def n_nodes(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def n_elements(self) -> int:
        return int(self.elements.shape[0])

    @property
    def n_cohesive(self) -> int:
        return int(self.cohesive.shape[0])

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
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
        sets: dict[str, Array] = {"EALL": np.arange(self.n_elements, dtype=np.int64)}
        for index, bead_id in enumerate(self.bead_ids):
            sets[f"BEAD_{_sanitize(bead_id)}"] = np.flatnonzero(
                self.element_bead == index
            ).astype(np.int64)
        cohesive_indices = np.arange(
            self.n_elements, self.n_elements + self.n_cohesive, dtype=np.int64
        )
        sets["COHESIVE"] = cohesive_indices
        for kind in sorted(set(self.cohesive_kind)):
            mask = np.array(
                [value == kind for value in self.cohesive_kind], dtype=bool
            )
            sets[f"COH_{kind.upper()}"] = cohesive_indices[mask]
        return sets

    def summary(self) -> dict:
        return {
            "nodes": self.n_nodes,
            "solid_elements": self.n_elements,
            "cohesive_elements": self.n_cohesive,
            "beads": len(self.bead_ids),
            "resolution": self.resolution,
            "cohesive_area": float(self.cohesive_area.sum())
            if self.n_cohesive
            else 0.0,
        }


def split_mesh(mesh: VoxelMesh) -> SplitMesh:
    """Cut the voxel mesh along bead interfaces and insert cohesive elements."""

    if mesh.n_elements == 0:
        raise ValueError("cannot split an empty mesh")
    elements = mesh.elements
    bead_of = mesh.element_bead

    parent = list(range(mesh.n_elements))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    face_map: dict[tuple[int, ...], tuple[int, int]] = {}
    interfaces: list[tuple[int, int, int]] = []  # (elem_a, face_index_a, elem_b)
    for element_index, element in enumerate(elements):
        for face_index, face in enumerate(HEX_FACES):
            quad = tuple(int(element[i]) for i in face)
            key = tuple(sorted(quad))
            other = face_map.get(key)
            if other is None:
                face_map[key] = (element_index, face_index)
                continue
            other_index, _ = other
            if other_index == element_index:
                continue
            if bead_of[other_index] == bead_of[element_index]:
                union(other_index, element_index)
            else:
                interfaces.append((other_index, face_index, element_index))

    roots: dict[int, int] = {}
    component = np.empty(mesh.n_elements, dtype=np.int64)
    for element_index in range(mesh.n_elements):
        root = find(element_index)
        if root not in roots:
            roots[root] = len(roots)
        component[element_index] = roots[root]

    nodes = mesh.nodes
    node_map: dict[tuple[int, int], int] = {}
    coords: list[tuple[float, float, float]] = []

    def node_copy(node: int, comp: int) -> int:
        key = (node, comp)
        existing = node_map.get(key)
        if existing is not None:
            return existing
        index = len(coords)
        node_map[key] = index
        x, y, z = nodes[node]
        coords.append((float(x), float(y), float(z)))
        return index

    solid = np.empty_like(elements)
    for element_index, element in enumerate(elements):
        comp = int(component[element_index])
        for position, node in enumerate(element):
            solid[element_index, position] = node_copy(int(node), comp)

    cohesive = np.empty((len(interfaces), 8), dtype=np.int64)
    cohesive_kind: list[str] = []
    cohesive_area = np.empty(len(interfaces), dtype=float)
    interface_ids: list[str] = []
    for index, (elem_a, face_index, elem_b) in enumerate(interfaces):
        face = HEX_FACES[face_index]
        comp_a = int(component[elem_a])
        comp_b = int(component[elem_b])
        for position, local in enumerate(face):
            node = int(elements[elem_a, local])
            cohesive[index, position] = node_copy(node, comp_a)
            cohesive[index, position + 4] = node_copy(node, comp_b)
        kind = (
            "interlayer"
            if mesh.element_layer[elem_a] != mesh.element_layer[elem_b]
            else "intralayer"
        )
        cohesive_kind.append(kind)
        cohesive_area[index] = mesh.resolution ** 2
        pair = sorted(
            (int(bead_of[elem_a]), int(bead_of[elem_b]))
        )
        interface_ids.append(
            "iface_"
            + mesh.bead_ids[pair[0]]
            + "__"
            + mesh.bead_ids[pair[1]]
        )

    return SplitMesh(
        nodes=np.asarray(coords, dtype=float),
        elements=solid,
        cohesive=cohesive,
        element_bead=mesh.element_bead.copy(),
        cohesive_area=cohesive_area,
        cohesive_kind=tuple(cohesive_kind),
        bead_ids=mesh.bead_ids,
        resolution=mesh.resolution,
        origin=mesh.origin,
        element_layer=mesh.element_layer.copy(),
        element_role=mesh.element_role.copy(),
        interface_ids=tuple(interface_ids),
        meta=dict(mesh.meta),
    )


def write_abaqus_cohesive(
    split: SplitMesh,
    card: CohesiveCard,
    path: str | Path,
    node_sets: dict[str, Array] | None = None,
    displacement: float = 0.5,
    title: str = "GCode-FEA cohesive coupon",
    interfaces: list[Interface] | None = None,
) -> Path:
    lines = [
        "*HEADING",
        title,
        "** generated by gcode-fea; units: mm, MPa, N",
        "** zero-thickness COH3D8 interfaces, constitutive thickness 1.0",
        "*NODE, NSET=NALL",
    ]
    for index, (x, y, z) in enumerate(split.nodes, start=1):
        lines.append(f"{index}, {x:.9g}, {y:.9g}, {z:.9g}")

    lines.append("*ELEMENT, TYPE=C3D8, ELSET=EALL")
    for index, element in enumerate(split.elements, start=1):
        nodes = ", ".join(str(int(node) + 1) for node in element)
        lines.append(f"{index}, {nodes}")

    offset = split.n_elements
    lines.append("*ELEMENT, TYPE=COH3D8, ELSET=COHESIVE")
    for index, element in enumerate(split.cohesive, start=1):
        nodes = ", ".join(str(int(node) + 1) for node in element)
        lines.append(f"{offset + index}, {nodes}")

    for name, indices in split.element_sets().items():
        if name in ("EALL",):
            continue
        lines.extend(_chunked_set("ELSET", name, indices + 1))

    groups = _cohesive_groups(split, interfaces)
    for key, member_indices in groups.items():
        name = _scale_set_name(key)
        lines.extend(
            _chunked_set("ELSET", name, offset + np.asarray(member_indices) + 1)
        )

    lines.extend(
        [
            "*SOLID SECTION, ELSET=EALL, MATERIAL=PLA",
            "*MATERIAL, NAME=PLA",
            "*ELASTIC",
            "3901.000000, 0.300000",
            "*DENSITY",
            "1.24e-09",
        ]
    )
    for key in groups:
        name = _scale_material_name(key)
        lines.extend(
            [
                "*COHESIVE SECTION, ELSET=" + _scale_set_name(key)
                + ", MATERIAL=" + name
                + ", RESPONSE=TRACTION SEPARATION, THICKNESS=SPECIFIED",
                f"{card.thickness:.6f},",
                f"*MATERIAL, NAME={name}",
                "*ELASTIC, TYPE=TRACTION",
                (
                    f"{card.stiffness_n * key:.6f}, "
                    f"{card.stiffness_s * key:.6f}, "
                    f"{card.stiffness_s * key:.6f}"
                ),
                "*DAMAGE INITIATION, CRITERION=QUADS",
                f"{card.sigma_n * key:.6f}, {card.sigma_s * key:.6f}, "
                f"{card.sigma_s * key:.6f}",
                "*DAMAGE EVOLUTION, TYPE=ENERGY, SOFTENING=LINEAR, "
                "MIXED MODE BEHAVIOR=BK",
                f"{card.G_Ic * key:.6f}, {card.G_IIc * key:.6f}, "
                f"{card.G_IIc * key:.6f}",
            ]
        )
    if card.damage_initiation not in (
        "quadratic_traction",
        "quadratic_stress",
    ):
        raise ValueError(
            f"unsupported damage initiation for Abaqus: {card.damage_initiation}"
        )
    if card.damage_evolution not in ("energy_linear", "energy"):
        raise ValueError(
            f"unsupported damage evolution for Abaqus: {card.damage_evolution}"
        )

    lines.extend(_node_set_blocks(node_sets or {}))
    lines.extend(_step_block(node_sets or {}, displacement))
    return _write(path, lines)


def write_cohesive_job(
    coupon,
    mesh: VoxelMesh,
    out_dir: str | Path,
    card: CohesiveCard,
    displacement: float = 0.5,
    interfaces: list[Interface] | None = None,
) -> dict:
    """Write a complete Abaqus cohesive job plus interface and card metadata."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if interfaces is None:
        interfaces = extract_interfaces(
            coupon.model, mesh=mesh, resolution=mesh.resolution
        )
    split = split_mesh(mesh)
    node_sets = coupon_node_sets(split)
    model_path = write_abaqus_cohesive(
        split,
        card,
        out / "model.inp",
        node_sets=node_sets,
        displacement=displacement,
        interfaces=interfaces,
    )
    interfaces_path = out / "interfaces.json"
    interfaces_path.write_text(
        json.dumps(
            {
                "schema": "gcode_fea.interfaces.v1",
                "report": interface_report(interfaces),
                "interfaces": [item.to_dict() for item in interfaces],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    card_path = out / "cohesive_card.json"
    card_path.write_text(
        json.dumps(card.as_card(), indent=2, sort_keys=True) + "\n"
    )

    result = {
        "schema": "gcode_fea.cohesive_job.v1",
        "solver": "abaqus",
        "coupon": coupon.spec.to_dict(),
        "interface_report": interface_report(interfaces),
        "mesh": split.summary(),
        "cohesive": card.as_card(),
        "node_sets": {name: int(len(indices)) for name, indices in node_sets.items()},
        "files": {
            "model": str(model_path),
            "interfaces": str(interfaces_path),
            "cohesive_card": str(card_path),
        },
        "notes": [
            "Cohesive areas are scaled from the contact model, not from raw voxel faces.",
            "Cohesive thickness is constitutive (1.0); separations are strain-like.",
            "CalculiX cohesive input is not generated yet.",
        ],
    }
    job_path = out / "job.json"
    job_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    result["files"]["job"] = str(job_path)
    return result


def _step_block(node_sets: dict[str, Array], displacement: float) -> list[str]:
    lines = ["*STEP, NAME=LOAD, NLGEOM=NO", "*STATIC", "0.05, 1.0", "*BOUNDARY"]
    for name, dof in (
        ("LEFT_CENTER", 1),
        ("RIGHT_CENTER", 1),
        ("LEFT_CORNERS", 2),
        ("RIGHT_CORNERS", 2),
        ("RIGHT_EDGE", 3),
    ):
        if name in node_sets and len(node_sets[name]) > 0:
            lines.append(f"{name}, {dof}, {dof}, 0.0")
    if "LEFT_EDGE" in node_sets and len(node_sets["LEFT_EDGE"]) > 0:
        lines.extend(["*BOUNDARY", f"LEFT_EDGE, 1, 1, {displacement:.6f}"])
    lines.extend(
        [
            "*OUTPUT, FIELD, FREQUENCY=1",
            "*ELEMENT OUTPUT",
            "S, E, SDEG, STATUS",
            "*NODE OUTPUT",
            "U, RF",
            "*END STEP",
        ]
    )
    return lines


def _node_set_blocks(node_sets: dict[str, Array]) -> list[str]:
    lines: list[str] = []
    for name, indices in node_sets.items():
        if len(indices) == 0:
            continue
        lines.extend(_chunked_set("NSET", name, indices))
    return lines


def _cohesive_groups(
    split: SplitMesh,
    interfaces: list[Interface] | None,
) -> dict[float, list[int]]:
    """Group cohesive elements by the contact-area scale factor.

    Scaling stiffness, strength, and energy by ``A_contact / A_face`` makes a
    full-size cohesive element reproduce the force and energy of the true
    contact patch. Rounding keeps the material count small.
    """

    scale_by_id: dict[str, float] = {}
    if interfaces is not None:
        scale_by_id = {
            item.interface_id: item.area_scale for item in interfaces
        }
    groups: dict[float, list[int]] = {}
    for index, interface_id in enumerate(split.interface_ids):
        scale = scale_by_id.get(interface_id, 1.0)
        key = round(scale if scale > 0 else 1.0, 4)
        groups.setdefault(key, []).append(index)
    if not groups:
        groups[1.0] = []
    return dict(sorted(groups.items()))


def _scale_set_name(scale: float) -> str:
    return "COH_SCALE_" + f"{int(round(scale * 10000)):05d}"


def _scale_material_name(scale: float) -> str:
    return "CZM_" + f"{int(round(scale * 10000)):05d}"


def _sanitize(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "_" for character in value
    )
    return cleaned.upper().strip("_") or "UNNAMED"
