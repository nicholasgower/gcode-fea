"""CLI for cohesive interface jobs and the traction-separation sensitivity."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from .benchmark import CouponSpec, build_coupon
from .cohesive import INITIAL_PLA_COHESIVE, CohesiveCard, calibration_plan, sensitivity_study
from .interfaces import extract_interfaces, interface_report
from .solvers.cohesive_input import write_cohesive_job
from .voxel import voxelize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build cohesive interface jobs (Abaqus COH3D8) and a "
            "traction-separation sensitivity matrix."
        )
    )
    parser.add_argument("--out", type=Path, default=Path("out/cohesive"))
    parser.add_argument("--angles", default="0,30,45,60,90")
    parser.add_argument("--length", type=float, default=10.0)
    parser.add_argument("--width", type=float, default=4.0)
    parser.add_argument("--bead-width", type=float, default=0.4)
    parser.add_argument("--bead-height", type=float, default=0.2)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--c-h", type=float, default=0.7)
    parser.add_argument("--c-v", type=float, default=0.2)
    parser.add_argument("--resolution", type=float, default=0.1)
    parser.add_argument("--displacement", type=float, default=0.5)
    parser.add_argument(
        "--card",
        type=Path,
        help="cohesive card JSON (default: the labelled initial PLA estimate)",
    )
    parser.add_argument("--no-sensitivity", action="store_true")
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

    card = INITIAL_PLA_COHESIVE
    if args.card is not None:
        try:
            card = _load_card(args.card)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"error: could not load cohesive card: {exc}", file=sys.stderr)
            return 1

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
            mesh = voxelize(coupon.model, resolution=args.resolution)
            interfaces = extract_interfaces(
                coupon.model, mesh=mesh, resolution=args.resolution
            )
            job = write_cohesive_job(
                coupon,
                mesh,
                out / f"coupon_{angle:03d}",
                card,
                displacement=args.displacement,
                interfaces=interfaces,
            )
            report = interface_report(interfaces)
            rows.append(
                {
                    "angle_deg": angle,
                    "mesh": job["mesh"],
                    "interfaces": report,
                    "files": job["files"],
                }
            )
            print(
                f"coupon {angle:3d} deg: {job['mesh']['solid_elements']} solids, "
                f"{job['mesh']['cohesive_elements']} cohesive elements, "
                f"interface area {report['total_area']:.3f} mm2"
            )
    except (ValueError, OSError, MemoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "schema": "gcode_fea.cohesive_benchmark.v1",
        "card": card.as_card(),
        "calibration_plan": calibration_plan(),
        "coupons": rows,
    }
    if not args.no_sensitivity:
        payload["sensitivity"] = sensitivity_study(card)
    path = out / "cohesive_benchmark.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"summary: {path}")
    if not card.calibrated:
        print(
            "note: cohesive card is an initial estimate, not calibrated; "
            "see calibration_plan in the summary"
        )
    return 0


def _load_card(path: Path) -> CohesiveCard:
    payload = json.loads(path.read_text())
    if "material" in payload and isinstance(payload["material"], dict):
        payload = payload["material"]
    allowed = {field.name for field in fields(CohesiveCard)}
    values = {key: value for key, value in payload.items() if key in allowed}
    return CohesiveCard(**values)


if __name__ == "__main__":
    sys.exit(main())
