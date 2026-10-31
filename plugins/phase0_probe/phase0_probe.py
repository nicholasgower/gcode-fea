# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
#
# [tool.orcaslicer.plugin]
# name = "Phase 0 Probe"
# description = "Dump one layer of ExtrusionPath data at posSimplifyPath."
# author = "GCode-FEA"
# version = "0.0.1"
# ///
"""Phase 0 probe: dump one layer's extrusion paths from the live slicing graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import orca

PROBE_VERSION = 1
MAX_LAYERS = 200


class Phase0Probe(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self) -> str:
        return "Phase0 Probe"

    def execute(self, ctx) -> orca.ExecutionResult:
        if ctx.step != orca.slicing.Step.posSimplifyPath:
            return orca.ExecutionResult.success()

        obj = ctx.object
        if obj is None:
            print("phase0_probe: no object at posSimplifyPath", file=sys.stderr)
            return orca.ExecutionResult.success()

        storage = Path(str(orca.host.plugin.storage()))
        layers = []
        truncated = False
        for layer_index, layer in enumerate(obj.layers()):
            paths = _collect_paths(layer)
            if not paths:
                continue
            layers.append(
                {
                    "layer_index": layer_index,
                    "print_z": layer.print_z,
                    "slice_z": layer.slice_z,
                    "height": layer.height,
                    "path_count": len(paths),
                    "paths": paths,
                }
            )
            if len(layers) >= MAX_LAYERS:
                truncated = True
                break

        if not layers:
            print("phase0_probe: no extrusion paths found", file=sys.stderr)
            return orca.ExecutionResult.success()

        first = layers[0]
        phase0 = {
            "probe_version": PROBE_VERSION,
            "orcaslicer_version": ctx.orca_version,
            "object_id": obj.id(),
            "layer_print_z": first["print_z"],
            "layer_slice_z": first["slice_z"],
            "layer_height": first["height"],
            "path_count": first["path_count"],
            "paths": first["paths"],
        }
        phase0_path = storage / f"phase0_probe_obj{obj.id()}.json"
        phase0_path.write_text(json.dumps(phase0, indent=2))

        phase1 = {
            "probe_version": PROBE_VERSION + 1,
            "orcaslicer_version": ctx.orca_version,
            "object_id": obj.id(),
            "layer_count": len(layers),
            "truncated": truncated,
            "layers": layers,
        }
        out = storage / f"phase1_probe_obj{obj.id()}.json"
        out.write_text(json.dumps(phase1, indent=2))
        print(
            f"phase0_probe: wrote {phase0_path} and {out} "
            f"({len(layers)} layers, truncated={truncated})",
            file=sys.stderr,
        )
        return orca.ExecutionResult.success(
            f"phase1 probe wrote {out} ({len(layers)} layers)"
        )


def _collect_paths(layer) -> list[dict]:
    paths: list[dict] = []
    for region in layer.regions():
        for collection in (region.perimeters, region.fills):
            for path in collection.flatten_paths():
                points = np.asarray(path.points())
                if points.size == 0:
                    continue
                points_mm = [
                    [orca.slicing.unscale(int(value)) for value in row]
                    for row in points
                ]
                paths.append(
                    {
                        "role": path.role,
                        "width": path.width,
                        "height": path.height,
                        "mm3_per_mm": path.mm3_per_mm,
                        "points_mm": points_mm,
                    }
                )
    return paths


@orca.plugin
class Phase0ProbePlugin(orca.base):
    def register_capabilities(self) -> None:
        orca.register_capability(Phase0Probe)
