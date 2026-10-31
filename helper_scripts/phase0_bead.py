#!/usr/bin/env python3
"""Build and verify one Phase 0 bead from the probe JSON using gmsh/OCCT."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import gmsh
except ImportError:  # pragma: no cover - exercised only without the dependency
    print(
        "gmsh is required. Run with:\n"
        "  uv run --with gmsh python helper_scripts/phase0_bead.py",
        file=sys.stderr,
    )
    raise SystemExit(2)

DEFAULT_PROBE_DIR = (
    Path.home()
    / ".config/OrcaSlicer"
    / "orca_plugins"
    / "plugin_data"
    / "phase0_probe"
)
DEFAULT_STEP = Path("out") / "phase0_bead.step"
DEFAULT_REPORT = Path("out") / "phase0_bead.report.json"
VOLUME_TOLERANCE = 1e-3  # 0.1%
DIMENSION_TOLERANCE = 1e-3  # mm


def newest_probe(directory: Path) -> Path:
    candidates = sorted(
        directory.glob("phase0_probe_obj*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no phase0_probe_obj*.json in {directory}")
    return candidates[0]


def select_segment(
    probe: dict,
    path_index: int | None = None,
    segment_index: int | None = None,
) -> tuple[int, int, dict, list[float], list[float]]:
    if path_index is not None:
        path = probe["paths"][path_index]
        if segment_index is None:
            segment_index = 0
        points = path["points_mm"]
        if segment_index + 1 >= len(points):
            raise IndexError("segment index out of range for the selected path")
        return path_index, segment_index, path, points[segment_index], points[segment_index + 1]

    best: tuple[float, int, int, dict, list[float], list[float]] | None = None
    for pi, path in enumerate(probe["paths"]):
        points = path["points_mm"]
        for si in range(len(points) - 1):
            a, b = points[si], points[si + 1]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if best is None or length > best[0]:
                best = (length, pi, si, path, a, b)
    if best is None or best[0] <= 0:
        raise ValueError("probe has no non-zero straight segment")
    _, pi, si, path, a, b = best
    return pi, si, path, a, b


def rotate_point(x: float, y: float, z: float, angle: float, origin: list[float]) -> tuple[float, float, float]:
    c, s = math.cos(angle), math.sin(angle)
    return (
        x * c - y * s + origin[0],
        x * s + y * c + origin[1],
        z + origin[2],
    )


def expected_bbox(length: float, width: float, height: float, angle: float, origin: list[float]) -> tuple[float, ...]:
    corners = [
        rotate_point(x, y, z, angle, origin)
        for x in (0.0, length)
        for y in (-width / 2, width / 2)
        for z in (0.0, height)
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    zs = [p[2] for p in corners]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def build_step(
    step_path: Path,
    length: float,
    width: float,
    height: float,
    angle: float,
    origin: list[float],
) -> tuple[float, tuple[float, ...]]:
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("phase0_bead")
    tag = gmsh.model.occ.addBox(0, -width / 2, 0, length, width, height)
    gmsh.model.occ.rotate([(3, tag)], 0, 0, 0, 0, 0, 1, angle)
    gmsh.model.occ.translate([(3, tag)], origin[0], origin[1], origin[2])
    gmsh.model.occ.synchronize()
    volume = gmsh.model.occ.getMass(3, tag)
    bbox = tuple(gmsh.model.getBoundingBox(-1, -1))
    step_path.parent.mkdir(parents=True, exist_ok=True)
    gmsh.write(str(step_path))
    # OCCT emits trailing spaces on several STEP records; keep the fixture clean.
    text = step_path.read_text()
    step_path.write_text("\n".join(line.rstrip() for line in text.splitlines()) + "\n")
    gmsh.finalize()
    return volume, bbox


def verify_step(step_path: Path) -> tuple[int, float, tuple[float, ...]]:
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("phase0_verify")
    gmsh.open(str(step_path))
    solids = gmsh.model.getEntities(3)
    if len(solids) != 1:
        gmsh.finalize()
        raise ValueError(f"expected 1 solid in STEP, found {len(solids)}")
    dim, tag = solids[0]
    volume = gmsh.model.occ.getMass(dim, tag)
    bbox = tuple(gmsh.model.getBoundingBox(-1, -1))
    gmsh.finalize()
    return len(solids), volume, bbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and verify one Phase 0 bead from the probe JSON."
    )
    parser.add_argument(
        "--probe",
        type=Path,
        help=f"probe JSON (default: newest in {DEFAULT_PROBE_DIR})",
    )
    parser.add_argument("--path-index", type=int)
    parser.add_argument("--segment-index", type=int)
    parser.add_argument("--step", type=Path, default=DEFAULT_STEP)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    probe_path = args.probe or newest_probe(DEFAULT_PROBE_DIR)
    probe = json.loads(probe_path.read_text())
    path_index, segment_index, path, a, b = select_segment(
        probe, args.path_index, args.segment_index
    )

    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= 0:
        print("error: selected segment has zero length", file=sys.stderr)
        return 1
    height = float(path["height"])
    area = float(path["mm3_per_mm"])
    if height <= 0 or area <= 0:
        print("error: path height/area must be positive", file=sys.stderr)
        return 1
    width = area / height
    expected_volume = area * length
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    origin = [a[0], a[1], a[2]]

    try:
        step_volume, step_bbox = build_step(
            args.step, length, width, height, angle, origin
        )
        solid_count, import_volume, import_bbox = verify_step(args.step)
    except Exception as exc:  # gmsh raises various exception types
        print(f"error: {exc}", file=sys.stderr)
        return 1

    volume_error = abs(import_volume - expected_volume) / expected_volume
    expected = expected_bbox(length, width, height, angle, origin)
    bbox_error = max(abs(a - b) for a, b in zip(expected, import_bbox))
    nominal_width = float(path["width"])
    width_delta_pct = 100.0 * (width - nominal_width) / nominal_width

    ok = (
        solid_count == 1
        and volume_error <= VOLUME_TOLERANCE
        and bbox_error <= DIMENSION_TOLERANCE
    )
    report = {
        "ok": ok,
        "probe": str(probe_path),
        "step": str(args.step),
        "orcaslicer_version": probe.get("orcaslicer_version"),
        "object_id": probe.get("object_id"),
        "layer_print_z": probe.get("layer_print_z"),
        "layer_height": probe.get("layer_height"),
        "path_index": path_index,
        "segment_index": segment_index,
        "role": path["role"],
        "nominal_width": nominal_width,
        "derived_width": width,
        "width_delta_pct": width_delta_pct,
        "bead_height": height,
        "length": length,
        "area_per_length": area,
        "expected_volume": expected_volume,
        "build_volume": step_volume,
        "step_volume": import_volume,
        "volume_rel_error": volume_error,
        "expected_bbox": expected,
        "step_bbox": import_bbox,
        "bbox_max_abs_error": bbox_error,
        "solid_count": solid_count,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))

    print(f"probe: {probe_path}")
    print(f"path {path_index} segment {segment_index}: {path['role']}")
    print(
        f"bead: length={length:.6f} mm width={width:.6f} mm "
        f"height={height:.6f} mm"
    )
    print(
        f"nominal width={nominal_width:.6f} mm, derived width delta="
        f"{width_delta_pct:+.3f}%"
    )
    print(
        f"volume: expected={expected_volume:.9f} mm3, step={import_volume:.9f} mm3, "
        f"rel_error={volume_error:.3e}"
    )
    print(f"bbox max abs error={bbox_error:.3e} mm; solids={solid_count}")
    print(f"step: {args.step}")
    print(f"report: {args.report}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
