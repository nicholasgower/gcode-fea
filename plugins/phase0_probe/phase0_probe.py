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

        for layer in obj.layers():
            paths = _collect_paths(layer)
            if not paths:
                continue

            data = {
                "probe_version": PROBE_VERSION,
                "orcaslicer_version": ctx.orca_version,
                "object_id": obj.id(),
                "layer_print_z": layer.print_z,
                "layer_slice_z": layer.slice_z,
                "layer_height": layer.height,
                "path_count": len(paths),
                "paths": paths,
            }
            storage = Path(str(orca.host.plugin.storage()))
            out = storage / f"phase0_probe_obj{obj.id()}.json"
            out.write_text(json.dumps(data, indent=2))
            print(
                f"phase0_probe: wrote {out} ({len(paths)} paths, "
                f"layer z={layer.print_z:.3f})",
                file=sys.stderr,
            )
            return orca.ExecutionResult.success(f"phase0 probe wrote {out}")

        print("phase0_probe: no extrusion paths found", file=sys.stderr)
        return orca.ExecutionResult.success()


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
