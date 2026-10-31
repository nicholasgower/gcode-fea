"""Coupon job assembly: mesh, node sets, solver input, and deletion recipe."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..benchmark import Coupon
from ..materials import Material
from ..voxel import VoxelMesh, interface_patches, write_mesh_summary
from .element_deletion import PAPER_DELETION_FRACTION, write_deletion_plan
from .inp import write_abaqus, write_calculix

Array = np.ndarray


def coupon_node_sets(mesh: VoxelMesh, tolerance: float | None = None) -> dict[str, Array]:
    """Grip node sets matching the benchmark boundary conditions.

    All sets are 1-based node IDs, ready for Abaqus/CalculiX. The load axis is
    +x, the right edge is fixed in z, and the left edge receives the imposed
    displacement.
    """

    nodes = mesh.nodes
    xmin, ymin, _, xmax, ymax, _ = mesh.bbox
    tol = tolerance if tolerance is not None else 1.5 * mesh.resolution
    x = nodes[:, 0]
    y = nodes[:, 1]
    left = np.flatnonzero(np.abs(x - xmin) <= tol)
    right = np.flatnonzero(np.abs(x - xmax) <= tol)
    left_y = y[left]
    right_y = y[right]
    y_center = (ymin + ymax) / 2.0
    y_tol = max(tol, (ymax - ymin) * 0.05)

    def ids(indices: Array) -> Array:
        return (indices + 1).astype(np.int64)

    return {
        "LEFT_EDGE": ids(left),
        "RIGHT_EDGE": ids(right),
        "LEFT_CENTER": ids(left[np.abs(left_y - y_center) <= y_tol]),
        "RIGHT_CENTER": ids(right[np.abs(right_y - y_center) <= y_tol]),
        "LEFT_CORNERS": ids(
            left[(np.abs(left_y - ymin) <= y_tol) | (np.abs(left_y - ymax) <= y_tol)]
        ),
        "RIGHT_CORNERS": ids(
            right[(np.abs(right_y - ymin) <= y_tol) | (np.abs(right_y - ymax) <= y_tol)]
        ),
    }


def write_coupon_job(
    coupon: Coupon,
    mesh: VoxelMesh,
    out_dir: str | Path,
    material: Material,
    solver: str = "calculix",
    displacement: float = 0.5,
    deletion_fraction: float = PAPER_DELETION_FRACTION,
    include_interfaces: bool = False,
) -> dict:
    """Write solver input, node sets, mesh summary, and deletion plan."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    node_sets = coupon_node_sets(mesh)
    material_card = material.as_card()
    if solver == "abaqus":
        model_path = write_abaqus(
            mesh,
            out / "model.inp",
            material_card,
            node_sets=node_sets,
            displacement=displacement,
        )
    elif solver == "calculix":
        model_path = write_calculix(
            mesh,
            out / "model.inp",
            material_card,
            node_sets=node_sets,
            displacement=displacement,
        )
    else:
        raise ValueError("solver must be 'abaqus' or 'calculix'")

    write_deletion_plan(
        out / "deletion_plan.json",
        fraction=deletion_fraction,
    )
    write_mesh_summary(mesh, out / "voxel_mesh.json")

    result = {
        "schema": "gcode_fea.coupon_job.v1",
        "solver": solver,
        "coupon": coupon.spec.to_dict(),
        "boundary_conditions": coupon.boundary_conditions,
        "material": material_card,
        "mesh": mesh.summary(),
        "volume_relative_error": (
            abs(mesh.volume - coupon.model.volume) / coupon.model.volume
            if coupon.model.volume > 0
            else None
        ),
        "node_sets": {
            name: int(len(indices)) for name, indices in node_sets.items()
        },
        "displacement": displacement,
        "deletion_fraction": deletion_fraction,
        "files": {
            "model": str(model_path),
            "deletion_plan": str(out / "deletion_plan.json"),
            "mesh_summary": str(out / "voxel_mesh.json"),
        },
    }
    if include_interfaces:
        patches = interface_patches(mesh)
        result["interfaces"] = [patch.to_dict() for patch in patches]

    job_path = out / "job.json"
    job_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    result["files"]["job"] = str(job_path)
    return result


def interface_summary(mesh: VoxelMesh) -> dict:
    patches = interface_patches(mesh)
    intralayer = [patch for patch in patches if patch.kind == "intralayer"]
    interlayer = [patch for patch in patches if patch.kind == "interlayer"]
    return {
        "patch_count": len(patches),
        "intralayer_count": len(intralayer),
        "interlayer_count": len(interlayer),
        "intralayer_area": sum(patch.area for patch in intralayer),
        "interlayer_area": sum(patch.area for patch in interlayer),
    }
