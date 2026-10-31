"""CLI for the paper benchmark coupons and solver job generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .benchmark import ANGLES, CouponSpec, build_coupon, compare_results, coupon_metrics
from .materials import PAPER_PLA
from .solvers.coupon import interface_summary, write_coupon_job
from .voxel import voxelize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build Seibel & Kiendl unidirectional coupons, voxel meshes, "
            "and Abaqus/CalculiX input files."
        )
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out/benchmark"),
        help="output directory",
    )
    parser.add_argument(
        "--angles",
        default=",".join(str(angle) for angle in ANGLES),
        help="comma-separated strand angles in degrees (default 0,30,45,60,90)",
    )
    parser.add_argument("--length", type=float, default=50.0, help="gauge length (mm)")
    parser.add_argument("--width", type=float, default=10.0, help="gauge width (mm)")
    parser.add_argument("--bead-width", type=float, default=0.4)
    parser.add_argument("--bead-height", type=float, default=0.2)
    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--c-h", type=float, default=0.7)
    parser.add_argument("--c-v", type=float, default=0.2)
    parser.add_argument(
        "--resolution",
        type=float,
        default=0.1,
        help="voxel size (mm); the default resolves one bead with 4x2 voxels",
    )
    parser.add_argument(
        "--solver",
        choices=("abaqus", "calculix", "none"),
        default="calculix",
        help="write solver input for this solver (default calculix)",
    )
    parser.add_argument(
        "--displacement",
        type=float,
        default=0.5,
        help="imposed grip displacement in mm",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="JSON results file with {angle: {E, sigma_f}} to compare to the paper",
    )
    parser.add_argument(
        "--criterion",
        choices=("rankine", "von_mises"),
        default="rankine",
    )
    parser.add_argument("--no-mesh", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        angles = [int(part) for part in args.angles.split(",") if part.strip()]
    except ValueError:
        print("error: angles must be comma-separated integers", file=sys.stderr)
        return 2
    if not angles:
        print("error: at least one angle is required", file=sys.stderr)
        return 2

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for angle in angles:
            spec = CouponSpec(
                angle_deg=angle,
                length=args.length,
                width=args.width,
                bead_width=args.bead_width,
                bead_height=args.bead_height,
                c_h=args.c_h,
                c_v=args.c_v,
                layers=args.layers,
            )
            coupon = build_coupon(angle, spec=spec)
            metrics = coupon_metrics(coupon, resolution=max(args.resolution, 0.2))
            row = {"angle_deg": angle, "metrics": metrics}
            if not args.no_mesh:
                mesh = voxelize(coupon.model, resolution=args.resolution)
                row["mesh"] = mesh.summary()
                row["interfaces"] = interface_summary(mesh)
                row["volume_relative_error"] = (
                    abs(mesh.volume - coupon.model.volume) / coupon.model.volume
                )
                if args.solver != "none":
                    job = write_coupon_job(
                        coupon,
                        mesh,
                        out / f"coupon_{angle:03d}",
                        PAPER_PLA,
                        solver=args.solver,
                        displacement=args.displacement,
                        include_interfaces=False,
                    )
                    row["job"] = job["files"]
                    row["node_sets"] = job["node_sets"]
            rows.append(row)
            print(
                f"coupon {angle:3d} deg: {metrics['strand_count']} strands, "
                f"volume {metrics['volume']:.3f} mm3"
            )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "schema": "gcode_fea.benchmark.v1",
        "material": PAPER_PLA.as_card(),
        "angles": angles,
        "coupons": rows,
    }
    if args.compare:
        try:
            results = json.loads(args.compare.read_text())
            summary["comparison"] = compare_results(
                {int(key): value for key, value in results.items()},
                criterion=args.criterion,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: could not read comparison results: {exc}", file=sys.stderr)
            return 1

    path = out / "benchmark.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"summary: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
