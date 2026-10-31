"""Command-line entry point for the GCode-FEA core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import load_cache, load_probe
from .contacts import detect_contacts
from .export import export_step, write_stl_model
from .report import write_report, write_sidecar
from .sweep import mesh_stats


def parse_roi(value: str | None) -> tuple[float, float, float, float, float, float] | None:
    if value is None:
        return None
    parts = [float(part) for part in value.split(",")]
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("ROI must be xmin,ymin,zmin,xmax,ymax,zmax")
    return (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])


def parse_layer_range(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = [int(part) for part in value.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("layer range must be first,last")
    return (parts[0], parts[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a bead mesostructure, contacts, sidecar, report, STL/STEP."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--probe", type=Path, help="Phase 0 probe JSON")
    source.add_argument("--cache", type=Path, help="GCode-FEA slice cache JSON")
    parser.add_argument("--out", type=Path, default=Path("out"), help="output directory")
    parser.add_argument(
        "--mode",
        choices=("volume_conserving", "nominal_envelope", "paper_octagon"),
        default="volume_conserving",
        help="cross-section mode for --probe input",
    )
    parser.add_argument("--c-h", type=float, default=0.7, help="flat top fraction")
    parser.add_argument("--c-v", type=float, default=0.225, help="flat side fraction")
    parser.add_argument("--roi", type=parse_roi, help="xmin,ymin,zmin,xmax,ymax,zmax")
    parser.add_argument("--layer-range", type=parse_layer_range, help="first,last layer index")
    parser.add_argument("--resolution", type=float, default=0.1, help="contact grid resolution (mm)")
    parser.add_argument("--join", choices=("segments", "miter"), default="segments")
    parser.add_argument(
        "--trim-overlaps",
        action="store_true",
        help="clip infill bead ends at wall footprints (requires shapely)",
    )
    parser.add_argument("--no-mesh", action="store_true", help="skip mesh generation/statistics")
    parser.add_argument("--no-contacts", action="store_true", help="skip contact detection")
    parser.add_argument("--stl", type=Path, help="write a binary STL to this path")
    parser.add_argument("--step", type=Path, help="write a STEP compound to this path")
    parser.add_argument(
        "--step-stride",
        type=int,
        default=1,
        help="use every Nth cross-section point in STEP (default 1)",
    )
    parser.add_argument(
        "--step-engine",
        choices=("auto", "gmsh", "planar", "merged"),
        default="auto",
        help="STEP writer: gmsh, pure-Python planar, or auto (default auto)",
    )
    parser.add_argument(
        "--step-tolerance",
        type=float,
        default=0.0,
        help="simplify curved profile sections within this many mm (0 = off)",
    )
    parser.add_argument(
        "--max-step-triangles",
        type=int,
        default=60_000,
        help="preflight limit for the merged STEP engine",
    )
    parser.add_argument("--sidecar", type=Path, help="sidecar path (default: <out>/beads.json)")
    parser.add_argument("--report", type=Path, help="report path (default: <out>/report.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.probe is not None:
            model = load_probe(
                args.probe,
                mode=args.mode,
                c_h=args.c_h,
                c_v=args.c_v,
            )
        else:
            model = load_cache(args.cache)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    model = model.filter_roi(args.roi, args.layer_range)
    if not model.beads:
        print("error: ROI contains no beads", file=sys.stderr)
        return 1
    trim_report = None
    if args.trim_overlaps:
        try:
            from .trim import trim_model

            model, trim_report = trim_model(model)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not model.beads:
            print("error: trimming removed every bead", file=sys.stderr)
            return 1

    contacts = []
    if not args.no_contacts:
        try:
            contacts = detect_contacts(model, resolution=args.resolution)
        except (ValueError, MemoryError) as exc:
            print(f"error: contact detection failed: {exc}", file=sys.stderr)
            return 1

    mesh_summary = None
    if not args.no_mesh:
        mesh_summary = mesh_stats(model, join=args.join)

    out = args.out
    sidecar_path = args.sidecar or (out / "beads.json")
    report_path = args.report or (out / "report.json")
    try:
        write_sidecar(model, contacts, sidecar_path, mesh_summary)
        write_report(
            model,
            contacts,
            report_path,
            resolution=args.resolution,
            mesh_summary=mesh_summary,
            roi=args.roi,
        )
    except OSError as exc:
        print(f"error: could not write outputs: {exc}", file=sys.stderr)
        return 1

    stl_path = None
    if args.stl is not None:
        try:
            stl_path = write_stl_model(model, args.stl, join=args.join)
        except (OSError, ValueError) as exc:
            print(f"error: STL export failed: {exc}", file=sys.stderr)
            return 1

    step_result = None
    if args.step is not None:
        try:
            step_result = export_step(
                model,
                args.step,
                verify=True,
                profile_stride=args.step_stride,
                step_engine=args.step_engine,
                step_tolerance=args.step_tolerance,
                max_triangles=args.max_step_triangles,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"error: STEP export failed: {exc}", file=sys.stderr)
            return 1

    report = json.loads(report_path.read_text())
    summary = report["summary"]
    print(f"beads: {summary['bead_count']} in {summary['layer_count']} layer(s)")
    print(f"volume: {summary['analytic_volume']:.6f} mm3")
    if summary["mesh_volume"] is not None:
        print(
            f"mesh volume: {summary['mesh_volume']:.6f} mm3 "
            f"(rel error {summary['volume_relative_error']:.3e})"
        )
        print(f"watertight: {summary['watertight_fraction']:.3%} of beads")
    print(
        f"contacts: {summary['contact_count']} "
        f"({summary['intralayer_count']} intralayer, "
        f"{summary['interlayer_count']} interlayer)"
    )
    if trim_report:
        print(
            f"trimmed: {trim_report['trimmed_beads']} bead(s), "
            f"volume change {trim_report['volume_relative_change']:.2%}"
        )
    print(f"sidecar: {sidecar_path}")
    print(f"report: {report_path} ({report['content_hash']})")
    if stl_path:
        print(f"stl: {stl_path}")
    if step_result:
        solid_count = step_result.get("solids", step_result.get("solid_count"))
        print(
            f"step: {step_result['path']} "
            f"({solid_count} solids via {step_result.get('engine', 'gmsh')}, "
            f"volume error {step_result.get('volume_relative_error')})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
