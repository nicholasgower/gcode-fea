# Phase 1 — Core geometry report

**Date:** 2026-10-31
**OrcaSlicer:** 2.5.0-beta build `345efec6`
**Reference printer for physical validation:** Creality Ender 3 V2 Neo
**Status:** Core MVP complete; dedicated multi-material and overhang fixtures pending.

## Implemented

| Module | Responsibility |
|---|---|
| `src/gcode_fea/model.py` | `CrossSection`, `Bead`, `Contact`, `SliceModel`, ROI filtering |
| `src/gcode_fea/cross_section.py` | Rounded barrel/octagon solver; volume-conserving, nominal-envelope, paper-octagon modes |
| `src/gcode_fea/cache.py` | Phase 0/Phase 1 probe ingestion and deterministic slice-cache JSON |
| `src/gcode_fea/sweep.py` | Segment-prism bead meshes, optional miter sweep, mesh volume and watertight checks |
| `src/gcode_fea/contacts.py` | 2.5D footprint-grid intralayer/interlayer contact detection |
| `src/gcode_fea/export.py` | Binary STL and gmsh/OCCT STEP compound export |
| `src/gcode_fea/report.py` | Sidecar and report generation, deterministic content hash |
| `src/gcode_fea/cli.py` | `python -m gcode_fea.cli` export pipeline |

The package is installable from `pyproject.toml` and depends on `numpy`.
STEP export is an optional `gmsh` extra (`uv sync --extra step` or
`uv run --with gmsh`).

## Geometry model

Each `ExtrusionPath` becomes a `Bead`:

- deposited area `A = mm3_per_mm`,
- height `h`,
- a solved cross-section with flat top/bottom and circular side arcs,
- a deterministic bead ID `obj<id>_L<layer>_P<path>`.

Cross-section modes:

- **volume_conserving** — fixed flat-top fraction `c_h`, solve the side bulge;
  the outer width emerges from the deposited volume.
- **nominal_envelope** — keep the nominal flat width; sides are concave when
  the deposited area is below the nominal rectangle. If the volume exceeds the
  nominal rectangle, the bead widens and a warning is recorded.
- **paper_octagon** — the paper's eight-sided shape with `c_h`/`c_v`; its area
  differs from the deposited area and material scaling remains external.

The Phase 0 probe showed `width * height` about 10% above `mm3_per_mm`; the
solver therefore uses the deposited area, not the nominal width.

## Mesh and join model

The default sweep is a **compound of straight segment prisms**:

- each segment is a closed prism with a polygonal cross-section and caps,
- the volume sum equals `A * path_length` within profile-sampling error,
- adjacent prisms overlap at corners and their caps are internal,
- fusing the compound is a downstream operation.

A `join="miter"` single-solid sweep exists for comparison; it loses volume at
sharp corners, so it is not the default.

## Contact model

Contacts are detected on a 2.5D raster per layer:

- intralayer: shared footprint boundary length × effective bead height,
- interlayer: overlapping footprint area × the smaller flat-top fraction,
- normals: horizontal from bead centers for intralayer, +Z for interlayer,
- centroids: weighted boundary/overlap pixel centers.

This is a geometric approximation. Closed-loop bead pairs produce one
aggregated contact with a single centroid; contact-patch splitting is future
work.

## Results

### Phase 0 probe (one layer)

- 3 beads, analytic volume `20.319839 mm³`
- mesh volume `20.309382 mm³` (relative error `5.15e-4`)
- watertight beads: `100%`
- contacts: `2` intralayer

### Real 20 mm cube (100 layers)

Input: `tests/fixtures/phase1/cube20_probe.json`, 0.2 mm layers, 2 walls,
20% sparse infill, app default printer.

- 390 beads in 100 layers
- analytic volume `3171.123597 mm³`
- mesh volume `3169.540936 mm³` (relative error `4.99e-4`)
- watertight beads: `100%`
- bbox `19.9966 × 19.9966 × 20.0000 mm`
- contacts: `789` (`386` intralayer, `403` interlayer)
- deterministic report hash, verified by repeated CLI runs in a fixed
  dependency environment (cross-version floating-point differences can change
  the hash)

### STEP export

Phase 0 probe, 61 segment prisms:

- volume error `5.15e-4`
- 61 solids
- `13 MB` at a 50-point profile
- `6.3 MB` with `--step-stride 2`, but volume error `2.0%`

STEP uses a polygonal profile, not smooth arc surfaces. Full-part STEP is not
practical; use ROI and/or a coarser stride with a volume-error check.

Since Phase 5 a pure-Python `planar` engine can also write STEP without gmsh
(`--step-engine planar`). It emits one AP214 `MANIFOLD_SOLID_BREP` per bead,
stitching rings at path vertices so the overlapping segment prisms become one
body. On the Phase 0 probe it produces 3 solids at 2.9 MB (stride 1, volume
error `5.1e-4`); `--step-tolerance 0.005` reduces that to 734 faces and 0.67 MB
at 1.3% volume error. The 20 mm cube is about 427k faces without simplification
and 103k at tolerance 0.005, so ROI/layer range remain essential.

## Acceptance checklist

- [x] Real 20 mm cube processed (390 beads, 100 layers).
- [~] Thin-wall: the cube has a two-wall perimeter; a dedicated thin-wall
  fixture is still needed.
- [~] Overhang/bridge: the cube contains an internal-bridge role; the no-droop
  assumption is documented, but a dedicated overhang/bridge coupon is pending.
- [ ] Multi-material: the model/report support material labels, but the plugin
  graph does not expose a per-path material ID yet.
- [x] Volume within 1% (mesh vs analytic: `0.05%`).
- [x] Envelope within one line width (`3.4 µm` bbox deviation).
- [x] >99.9% watertight (100% of beads; compounds, not fused).
- [x] Deterministic IDs and report hash.
- [~] Performance: 390 beads/100 layers run in seconds; no formal budget yet.
  STEP size is the binding constraint.

## Known limitations

1. Segment prisms are a compound, not a fused solid; joints contain internal
   caps.
2. Contacts are 2.5D and aggregate closed-loop pairs; no contact patches.
3. STEP profiles are polygonal, not smooth arcs; file size grows quickly.
4. Multi-material and per-path process data are not exposed by the current
   plugin graph.
5. Supports are not exposed; overhang droop is not modeled.
6. No volume-mesh (hex/tet) export yet; STL is the current mesh output.

## Reproduction

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v

PYTHONPATH=src python3 -m gcode_fea.cli \
  --probe tests/fixtures/phase1/cube20_probe.json \
  --out /tmp/phase1-cube20 --resolution 0.2

PYTHONPATH=src python3 -m gcode_fea.cli \
  --probe tests/fixtures/phase0/phase0_probe_obj102.json \
  --out /tmp/phase1-step --step /tmp/phase1-step/beads.step
```

## Next

Phase 2: reproduce the paper's 0/30/45/60/90° benchmark with the FEA recipe,
then improve contact patches, fused STEP export, multi-material data, and
volume meshing.
