"""CLI for process-informed adhesion.

Reads a sliced bead probe/cache and its G-code, extracts per-bead process data,
scores every interface, and writes the sidecar with bond quality plus an
uncertainty envelope.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adhesion import AdhesionModel, apply_bond_quality, quality_scaled_card, uncertainty_report
from .cache import load_cache, load_probe
from .cohesive import INITIAL_PLA_COHESIVE
from .contacts import detect_contacts
from .interfaces import extract_interfaces
from .process import attach_process, default_process, parse_gcode, process_report
from .report import write_report, write_sidecar
from .sweep import mesh_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Attach G-code process data to beads, score interface bond "
            "quality, and write the sidecar/uncertainty report."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--probe", type=Path, help="Phase 0/Phase 1 probe JSON")
    source.add_argument("--cache", type=Path, help="GCode-FEA slice cache JSON")
    parser.add_argument("--gcode", type=Path, help="sliced G-code for process data")
    parser.add_argument("--out", type=Path, default=Path("out/process"))
    parser.add_argument("--resolution", type=float, default=0.2)
    parser.add_argument("--tolerance", type=float, default=0.5)
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ambient", type=float, default=25.0)
    parser.add_argument("--activation-energy", type=float, default=5.0e4)
    parser.add_argument("--reference-time", type=float, default=5.0)
    parser.add_argument("--cooling-time", type=float, default=1.0)
    parser.add_argument("--fan-factor", type=float, default=0.02)
    parser.add_argument("--bond-window", type=float, default=5.0)
    parser.add_argument("--no-uncertainty", action="store_true")
    parser.add_argument("--with-mesh", action="store_true")
    parser.add_argument(
        "--scaled-card",
        type=Path,
        help="write a cohesive card scaled by the mean bond quality",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        model = (
            load_probe(args.probe)
            if args.probe is not None
            else load_cache(args.cache)
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    model_meta = model.meta
    gcode = None
    if args.gcode is not None:
        try:
            gcode = parse_gcode(args.gcode)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        bead_process, match = attach_process(
            model, gcode, tolerance=args.tolerance
        )
    else:
        default_temp = float(model_meta.get("nozzle_temp_c", 210.0))
        bead_process = default_process(model, nozzle_temp_c=default_temp)
        match = {
            "beads": len(model.beads),
            "matched": 0,
            "coverage": 0.0,
            "fallback": len(model.beads),
            "source": "default",
        }

    adhesion_model = AdhesionModel(
        ambient_temp_c=args.ambient,
        activation_energy_j_mol=args.activation_energy,
        reference_time_s=args.reference_time,
        cooling_time_s=args.cooling_time,
        fan_cooling_factor=args.fan_factor,
        bond_window_s=args.bond_window,
    )
    try:
        interfaces = extract_interfaces(
            model, resolution=args.resolution
        )
        interfaces, adhesion = apply_bond_quality(
            interfaces, bead_process, adhesion_model
        )
        uncertainty = (
            None
            if args.no_uncertainty
            else uncertainty_report(
                interfaces,
                bead_process,
                adhesion_model,
                samples=args.samples,
                seed=args.seed,
            )
        )
    except (ValueError, MemoryError) as exc:
        print(f"error: adhesion scoring failed: {exc}", file=sys.stderr)
        return 1

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    process_summary = process_report(model, bead_process)
    process_summary["match"] = match
    if gcode is not None:
        process_summary["gcode"] = gcode.summary()

    contacts = detect_contacts(model, resolution=args.resolution)
    mesh_summary = mesh_stats(model) if args.with_mesh else None
    try:
        write_sidecar(
            model,
            contacts,
            out / "beads.json",
            mesh_summary=mesh_summary,
            interfaces=interfaces,
            bead_process=bead_process,
            process_summary=process_summary,
            adhesion=adhesion,
        )
        write_report(
            model,
            contacts,
            out / "report.json",
            resolution=args.resolution,
            mesh_summary=mesh_summary,
            interfaces=interfaces,
            adhesion=adhesion,
        )
        (out / "process.json").write_text(
            json.dumps(
                {
                    "schema": "gcode_fea.process.v1",
                    "summary": process_summary,
                    "beads": [
                        record.as_dict() for record in bead_process.values()
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (out / "adhesion.json").write_text(
            json.dumps(
                {"schema": "gcode_fea.adhesion.v1", **adhesion},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if uncertainty is not None:
            (out / "uncertainty.json").write_text(
                json.dumps(uncertainty, indent=2, sort_keys=True) + "\n"
            )
    except OSError as exc:
        print(f"error: could not write outputs: {exc}", file=sys.stderr)
        return 1

    mean_quality = adhesion["quality"]["mean"]
    print(f"beads: {len(model.beads)}, interfaces: {len(interfaces)}")
    print(
        f"process coverage: {match['coverage']:.1%} "
        f"({match['matched']}/{match['beads']} beads)"
    )
    print(
        f"bond quality: mean {mean_quality:.3f}, "
        f"intralayer {adhesion['intralayer_quality']['mean']:.3f}, "
        f"interlayer {adhesion['interlayer_quality']['mean']:.3f}"
    )
    if uncertainty is not None:
        print(
            "uncertainty: mean quality "
            f"p05 {uncertainty['mean_quality']['p05']:.3f}, "
            f"median {uncertainty['mean_quality']['median']:.3f}, "
            f"p95 {uncertainty['mean_quality']['p95']:.3f}"
        )
    if args.scaled_card is not None:
        card = quality_scaled_card(INITIAL_PLA_COHESIVE, mean_quality, adhesion_model)
        args.scaled_card.parent.mkdir(parents=True, exist_ok=True)
        args.scaled_card.write_text(
            json.dumps(card.as_card(), indent=2, sort_keys=True) + "\n"
        )
        print(f"scaled cohesive card: {args.scaled_card}")
    if not adhesion_model.calibrated:
        print("note: adhesion model is uncalibrated; see out/uncertainty.json")
    print(f"outputs: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
