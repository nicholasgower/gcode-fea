# Phase 0 fixtures

These are the real artifacts from the Phase 0 integration spike on OrcaSlicer
2.5.0-beta build `345efec6` (2026-10-31).

- `phase0_probe_obj102.json` — one layer of `ExtrusionPath` data captured at
  `posSimplifyPath` from the 10 mm cube fixture. It contains three paths
  (`Inner wall`, `Outer wall`, `Bottom surface`) at `print_z = 0.2`,
  `height = 0.2`. It came from the app's default 200 × 200 × 100 printer
  preset, not the Ender 3 V2 Neo profile; it validates the data path, not a
  printer-specific calibration.
- `phase0_bead.step` — a single volume-conserving rectangular bead built from
  the longest straight segment (path 2, segment 26) with gmsh/OCCT.
- `phase0_bead.report.json` — verification data: length, width, height,
  expected/STEP volume, bbox, solid count, and errors.

Reproduce:

```bash
uv run --with gmsh python helper_scripts/phase0_bead.py \
  --probe tests/fixtures/phase0/phase0_probe_obj102.json \
  --step tests/fixtures/phase0/phase0_bead.step \
  --report tests/fixtures/phase0/phase0_bead.report.json
```

Expected result: `PASS`, one solid, volume relative error `8.4e-13`, maximum
bbox error `1e-7 mm`.

The bead is intentionally a rectangular volume-conserving approximation. The
slicer's nominal width (`0.456913 mm`) is 9.4% wider than the volume-derived
width (`0.413993 mm`); the rounded cross-section and contact model arrive in
Phase 1.
