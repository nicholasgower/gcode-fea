#!/usr/bin/env python3
"""Estimate STEP size and a layer budget before writing the file.

The merged writer refuses after the boolean union when the triangle count is too
large, and the planar writer only refuses on face count. This helper performs the
same preflight without writing a STEP, reports an estimated byte size, and
recommends a contiguous layer range.

Estimates use calibrated bytes-per-unit constants (planar: bytes per face,
merged: bytes per triangle) measured from the Phase 0 fixture. They are for
budgeting, not exact accounting.

The helper is read-only: it writes only ``--json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from gcode_fea.cache import load_cache, load_probe  # noqa: E402
from gcode_fea.config import PluginConfig  # noqa: E402

PLANAR_BYTES_PER_FACE = 970.0
MERGED_BYTES_PER_TRIANGLE = 780.0


def estimate(
    model,
    engine: str = "merged",
    tolerance: float = 0.0,
    profile_stride: int = 1,
    limit: int = 60_000,
    simplify: float = 0.0,
) -> dict:
    if limit < 1:
        raise ValueError("limit must be positive")
    if engine == "planar":
        from gcode_fea.step_planar import estimate_faces

        units = estimate_faces(model, profile_stride, tolerance)
        per_unit = PLANAR_BYTES_PER_FACE
        solids = len(model.beads)
        unit_name = "faces"
        dropped = 0
    elif engine == "merged":
        units, solids, dropped = _merged_counts(
            model, tolerance, profile_stride, simplify
        )
        per_unit = MERGED_BYTES_PER_TRIANGLE
        unit_name = "triangles"
    else:
        raise ValueError("engine must be 'planar' or 'merged'")

    layers = sorted(model.layers())
    layer_count = len(layers)
    per_layer = units / layer_count if layer_count else units
    recommended = None
    if per_layer > 0 and units > limit:
        fit = max(1, int(limit // per_layer))
        if fit < layer_count:
            recommended = [layers[0], layers[fit - 1]]

    return {
        "engine": engine,
        "unit": unit_name,
        "units": units,
        "solids": solids,
        "dropped_solids": dropped,
        "bytes_per_unit": per_unit,
        "estimated_bytes": int(units * per_unit),
        "limit": limit,
        "exceeds_limit": units > limit,
        "layer_count": layer_count,
        "units_per_layer": per_layer,
        "recommended_layer_range": recommended,
        "tolerance": tolerance,
        "profile_stride": profile_stride,
    }


def _merged_counts(model, tolerance: float, profile_stride: int, simplify: float):
    try:
        import manifold3d as m3
    except ImportError as exc:
        raise RuntimeError(
            "merged budget needs manifold3d; install `manifold3d>=3.5`"
        ) from exc

    from gcode_fea.merged_step import _solid_to_manifold
    from gcode_fea.step_planar import (
        _build_segment_solids,
        simplify_profile,
    )

    manifolds = []
    dropped = 0
    for bead in model.sorted_beads():
        profile = list(bead.cross_section.profile)
        if tolerance > 0:
            profile = simplify_profile(profile, tolerance)
        if profile_stride > 1:
            profile = profile[::profile_stride]
        if len(profile) < 3:
            dropped += 1
            continue
        for solid in _build_segment_solids(bead, profile):
            manifold = _solid_to_manifold(m3, solid)
            if manifold is None:
                dropped += 1
            else:
                manifolds.append(manifold)
    if not manifolds:
        raise ValueError("no bead solids could be converted")

    merged = m3.Manifold.batch_boolean(manifolds, m3.OpType.Add)
    if simplify > 0:
        merged = merged.simplify(simplify)
    mesh = merged.to_mesh()
    triangles = len(mesh.tri_verts)
    solids = len(merged.decompose())
    return triangles, solids, dropped


def _load(args):
    if args.probe is not None:
        return load_probe(
            args.probe,
            mode=args.mode,
            c_h=args.c_h,
            c_v=args.c_v,
        )
    model = load_cache(args.cache)
    if args.layer_range is not None:
        model = model.filter_roi(layer_range=tuple(args.layer_range))
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--probe", type=Path)
    source.add_argument("--cache", type=Path)
    parser.add_argument("--engine", choices=("merged", "planar"), default="merged")
    parser.add_argument("--tolerance", type=float, default=0.0)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--limit", type=int, default=60_000)
    parser.add_argument("--simplify", type=float, default=0.0)
    parser.add_argument("--mode", choices=("volume_conserving", "nominal_envelope", "paper_octagon"), default="volume_conserving")
    parser.add_argument("--c-h", type=float, default=0.7)
    parser.add_argument("--c-v", type=float, default=0.225)
    parser.add_argument("--layer-range", type=int, nargs=2)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    try:
        model = _load(args)
        report = estimate(
            model,
            engine=args.engine,
            tolerance=args.tolerance,
            profile_stride=args.stride,
            limit=args.limit,
            simplify=args.simplify,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(
        f"{report['engine']}: {report['units']:,} {report['unit']}, "
        f"about {report['estimated_bytes'] / 1e6:.2f} MB, "
        f"{report['solids']} solid(s)"
    )
    if report["exceeds_limit"]:
        print(
            f"over the {report['limit']:,}-{report['unit']} limit; "
            f"recommended layer range: {report['recommended_layer_range']}"
        )
    if report["dropped_solids"]:
        print(
            f"warning: {report['dropped_solids']} input bead/prism(s) would be dropped",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
