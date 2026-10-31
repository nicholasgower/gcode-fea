# ROADMAP.md — GCode-FEA

## Vision

Make mesostructure-resolved FEA of FFF parts practical by exporting the as-printed bead geometry and interface data directly from OrcaSlicer.

## Problem

Nominal CAD models miss the features that determine FFF strength: inter-bead voids, contact areas, strand orientation, layer stacking, and process-dependent bond quality. G-code post-processors recover paths but lose width/role/process context. Bead-resolved models are expensive. Adhesion is not geometry.

## Success criteria

- A user can slice in OrcaSlicer and export a region-of-interest STEP + sidecar + optional mesh.
- The paper's unidirectional benchmark is reproduced (strength trend and failure mode).
- A cohesive-interface workflow exists with a solver template and calibration guidance.
- A real part can be analyzed via ROI/submodeling without exporting the whole part at bead resolution.
- Outputs are deterministic, documented, tested, and reproducible.

## Guiding principles

1. Core first, plugin second.
2. As-printed geometry, not nominal.
3. Volume is the primary invariant.
4. Every approximation is explicit.
5. Separate contact area from bond quality.
6. Validate against the paper before extrapolating.
7. Scale by ROI, coarsening, and submodeling, not by brute force.

## Local environment constraint

Pinned environment, AppImage paths, plugin-manager facts, the headless CLI entrypoint, and the Ender 3 V2 Neo reference-printer details are in `AGENTS.md` §8 and §15. Summary:

- **Primary plugin target:** OrcaSlicer 2.5.0-beta build `345efec6` AppImage.
- **Secondary MCP target:** OrcaSlicer MCP 2.4.2-mcp.13 AppImage; optional, no 2.5 plugin API.
- **Headless path:** `--appimage-extract-and-run` + `--slice` + minimal no-inherit process with `G92 E0`, wrapped by `helper_scripts/orca_slice.py`.
- **Reference printer:** Creality Ender 3 V2 Neo; all physical coupons use its OrcaSlicer profile.

The 2.5 build is the only plugin target (`src/slic3r/plugin` is absent at `v2.4.2` and present at `v2.5.0-beta`). The MCP server remains optional and separate. The project-local `opencode.json` configures `orcaslicer-mcp`; it requires `ORCA_API_TOKEN` from the 2.4.2-mcp.13 fork and cannot drive the 2.5 build.

## Phase 0 — Integration spike

**Goal:** prove the data path and toolchain on one bead.

**Status (2026-10-31): complete.** The probe was discovered and loaded, ran at `posSimplifyPath`, and wrote one layer of `ExtrusionPath` data. The minimal bead STEP round-tripped through gmsh/OCCT as one solid with a `8.4e-13` relative volume error and `1e-7 mm` bounding-box error. See `docs/phase0_report.md`.

**Deliverables**

- A probe plugin installed under `~/.config/OrcaSlicer/orca_plugins/` that runs at `posSimplifyPath` and dumps one layer's `ExtrusionPath` data (points, width, height, `mm3_per_mm`, role). **Done.**
- A minimal bead sweep from that data. **Done** (`helper_scripts/phase0_bead.py`).
- A STEP export and round-trip in gmsh/OCCT; commercial FEA import pending. **Partially done.**
- A headless core CLI test path (`--appimage-extract-and-run`, `--slice`, minimal no-inherit process) with fixture profiles and models. **Done.** Plugin hooks are GUI-only in 2.5.0-beta.
- Helper scripts: environment doctor, headless slice wrapper, plugin sync, Phase 0 bead builder, with unit tests. **Done** (bead builder verified, not unit-tested).
- Toolchain decision: **gmsh/OCCT** for the Phase 0/Phase 1 prototype. `build123d` was not used because its dependency tree exceeded available disk space.

**Acceptance**

- The 2.5 plugin manager discovers the probe (`Found 1 plugin manifest`) and the hook runs on pinned build `345efec6`. **Pass.**
- Bead volume matches the deposited volume within 0.1%. **Pass** (`8.4e-13`). The nominal width differs from the volume-derived width by 9.4%; this is documented.
- The STEP imports as one watertight solid and has the expected bounding box. **Pass in gmsh/OCCT;** commercial FEA import is pending.
- The probe does not block slicing; outputs land in plugin storage or a permitted export folder. **Pass.**
- The core headless path is established and documented; the plugin-hook path is GUI-only and documented. **Pass.**

**Risks / findings**

- Plugin API churn; graph lifetime; scaled-coordinate mistakes; OCCT sweep issues.
- The 2.5 default profile needs a `G92 E0` layer-change override; 2.4.2 system bundles are not loaded by 2.5, so use self-contained process fixtures; audit prompts may appear for outputs outside `~/.config/OrcaSlicer`; the 2.5 app has fresh profiles.
- Plugin hooks do not run in the 2.5 headless CLI; GUI automation or a human is required for hook testing.
- A sideloaded plugin needs `.install_state.json` activation, and a preset needs both `slicing_pipeline_plugin` and the `plugins` reference (`<plugin_key>;;<capability_name>`).
- Slicer nominal width and volume-derived width can differ materially (9.4% in Phase 0); the cross-section must conserve `mm3_per_mm` and report the delta.

## Phase 1 — Core geometry

**Goal:** a complete, standalone geometry pipeline.

**Deliverables**

- Slice-cache schema and reader/writer.
- Cross-section solver for the rounded octagon/barrel; three volume modes.
- Sweep/mesh generation for straight and curved paths; joins; caps.
- gmsh/OCCT-backed STEP/mesh prototype behind an export interface.
- Contact detection: intralayer and interlayer; contact area, normal, centroid, gap/overlap.
- ROI: object, bbox, layer range, mesh volume.
- STEP export: faceted and analytic; bead compound, fused, glued, envelope.
- Optional volume mesh export with element sets.
- Sidecar and report.
- Unit/integration tests and small fixtures.

**Acceptance**

- 20 mm cube, thin-wall test, overhang/bridge test, multi-material test.
- Volume within 1%; envelope within one line width; >99.9% watertight.
- Deterministic IDs and report.
- Performance budget met for the agreed ROI size.

**Risks**

- Bead count; boolean cost; self-intersections; sparse-infill contact complexity; file size.

## Phase 2 — Paper benchmark

**Goal:** reproduce Seibel & Kiendl's unidirectional PLA results.

**Deliverables**

- Paper octagon mode with `c_h`, `c_v`, area scaling.
- Material card: `E = 3901 MPa`, `ν = 0.3`, `σ_f = 45.38 MPa`.
- Rankine criterion.
- Boundary conditions and load steps for the 0/30/45/60/90° coupons.
- Element-deletion + localization script for the target solver.
- Comparison report: `E`, `σ_f`, failure mode, angle trend.

**Acceptance**

- The strength trend decreases with angle; intralayer failure for non-0°; 0° behaves differently.
- `E` and `σ_f` within the agreed tolerance (target ~10–15%).
- The script runs to complete failure on the reduced model.
- `docs/paper_benchmark.md` records the comparison.

**Risks**

- Calibration mismatch between the paper's reduced-area model and the volume-conserving model.
- Mesh convergence and runtime.
- Solver-specific element deletion/localization.

## Phase 2b — Physical geometry validation (Ender 3 V2 Neo)

**Goal:** measure the as-printed geometry and compare it with the exported bead model on the reference printer.

**Coupons**

- 20 × 20 × 20 mm cube at 100% rectilinear infill (the checked-in 10 mm cube is a smoke test only).
- Thin-wall box (one or two perimeters).
- Overhang/bridge test.
- Cross-section coupon (single wall / 100% infill) for bead width, height, and contact lengths.
- Optional: 0/30/45/60/90° tensile coupons for the paper benchmark.

**Measurements**

- Mass with a calibrated scale; compare with predicted volume × filament density.
- Outer dimensions after cooling with calipers/micrometer; compare with the predicted envelope and nominal CAD.
- Cross-section under a microscope: bead width, height, `c_h`, `c_v`, void fraction.
- Overhang/bridge sag and voiding; compare with the no-droop assumption.
- Record the exact Ender 3 V2 Neo profile, filament, temperatures, fan, speed, flow, and layer height.

**Deliverables**

- Printable coupon set with slicer profiles and G-code.
- Ender 3 V2 Neo machine/process/filament profiles checked into `tests/fixtures/profiles/`.
- Measurement SOP and raw data.
- `docs/physical_validation.md` comparing measured vs. predicted geometry and contact statistics.
- Calibrated `c_h`/`c_v` starting values for the Ender 3 V2 Neo.

**Acceptance**

- Mass/volume within 1–3% after exclusions; outer dimensions within ±0.1–0.2 mm or one line width.
- Bead width/height within ~10%; `c_h`/`c_v` within ~15%; deviations explained.
- Overhang/bridge error quantified, not assumed away.
- At least three replicates per condition; scatter reported.

**Risks**

- Printer/filament variability; moisture; bed leveling; flow calibration; measurement uncertainty; destructive sectioning time.

## Phase 3 — Cohesive interfaces

**Goal:** replace the geometric proxy with explicit interface failure.

**Deliverables**

- Interface extraction from contacts: surfaces/elements, normals, areas.
- Cohesive material cards: `σ_n`, `σ_s`, `G_Ic`, `G_IIc`, stiffness, damage evolution.
- Solver templates: Abaqus and CalculiX (at least one end-to-end).
- Calibration workflow: peel/shear/DCB or paper-derived starting values.
- Sensitivity study: interface strength/toughness vs. macroscopic strength.
- Comparison to the geometric proxy.

**Acceptance**

- A coupon simulation develops a discrete crack at the expected interface.
- Mesh convergence at the interface.
- The solver template runs from a clean checkout with documented commands.
- The predicted trend is consistent with the paper/experiments.

**Risks**

- Interface mesh complexity; solver support; calibration data; mode mixity; runtime.

## Phase 4 — Process-informed adhesion

**Goal:** make interface properties spatially varying and predictive.

**Deliverables**

- Per-path process data: speed, temperature, fan, layer time (G-code parsing or new binding).
- Thermal/healing model: interface temperature history → bond degree → `σ`/`G`.
- Per-interface bond-quality field in the sidecar.
- Experimental validation at different layer times/temperatures/fan settings.
- Uncertainty report.

**Acceptance**

- Predicted strength trend matches experiments better than a global `c_v`.
- The model reports uncertainty and its calibration domain.
- The workflow is documented and reproducible.

**Risks**

- Thermal model complexity; missing sensors/calibration; material variability; runtime.

## Phase 5 — Plugin UX and packaging

**Goal:** a usable OrcaSlicer experience.

**Deliverables**

- Script capability: "Export FEA Geometry" with a dialog and progress.
- Pages tab (optional): slice status, export history, report viewer.
- Config: defaults, ROI, modes, output formats, solver.
- Cache management and stale-slice detection.
- Install/packaging: `.py` or `.whl`; dependencies; permissions.
- Include `LICENSE` and `CREDITS.md`; document the MIT/AGPL boundary for the bundled plugin.
- Docs: user guide, troubleshooting, examples.

**Acceptance**

- A user can slice, export, and open the result without a terminal.
- The export does not block the UI or silently freeze slicing.
- Errors are actionable and logged.
- The plugin declares only the permissions it needs.

**Risks**

- Experimental API; UI thread rules; audit prompts; dependency size.

## Cross-cutting workstreams

- **Performance:** bead count, triangles, file size, wall time; budgets per phase.
- **Validation:** volume, envelope, watertightness, STEP import, benchmark, regression.
- **Physical validation:** Ender 3 V2 Neo coupons, metrology, cross-sections, replicate scatter.
- **Plugin testing:** unit (core), integration (2.5 CLI + fixtures), plugin discovery/smoke (2.5 GUI/logs), GUI smoke (manual).
- **Documentation:** schema, assumptions, calibration, troubleshooting.
- **Licensing:** project MIT for original code; OrcaSlicer/MaxEllis fork AGPL-3.0; OCCT LGPL; gmsh GPL; audit combined-work and dependency obligations before distribution.
- **CI:** lint, unit tests, small integration fixtures; benchmark optional/nightly.

## Decision log

| ID | Decision | Status | Recommendation |
|---|---|---|---|
| D1 | Volume convention | Open | Implement all three; default volume-conserving; paper mode for validation |
| D2 | Adhesion ambition | Open | Phase 1 proxy; Phase 3 CZM; Phase 4 process-informed |
| D3 | Output contract | Open | STEP + sidecar + optional mesh |
| D4 | Integration | Open | Standalone core/CLI + thin plugin |
| D5 | Scope | Open | ROI default; supports excluded; multi-material supported |
| D6 | Target solver | Open | Abaqus + CalculiX templates |
| D7 | Dependencies | Open | OCCT/build123d or gmsh; audit licenses |
| D8 | Coordinate frame | Open | Printer frame, Z up; optional Y-up transform |
| D9 | Plugin target | Resolved | OrcaSlicer 2.5.0-beta build `345efec6`; 2.4.2-mcp.13 fork is the secondary MCP target |
| D10 | Headless CLI path | Resolved | `--appimage-extract-and-run` + `--slice` + minimal no-inherit process with `G92 E0` |
| D11 | MCP role | Resolved | Optional agent tooling for 2.4.2; not a plugin dependency |
| D12 | Reference printer | Resolved | Creality Ender 3 V2 Neo; use its OrcaSlicer profile for physical tests |
| D13 | Project license | Resolved | MIT for original code and documentation; paper stays CC BY 4.0; third-party licenses remain |
| D14 | Phase 0 toolchain | Resolved | gmsh/OCCT for the prototype; build123d not used due to dependency weight/disk |
| D15 | Plugin hook execution | Resolved | GUI-only in 2.5.0-beta; CLI for core tests, GUI/manual for plugin hooks |

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Plugin API churn | adapter breaks | pin version; adapter layer; core independent |
| Graph lifetime/threading | crashes/stale data | serialize during execute; no stashed refs |
| STEP size/meshing | unusable exports | ROI; faceted; mesh-first; coarsening |
| Boolean/sweep robustness | invalid solids | mesh-first; validation; fallbacks |
| Volume vs. paper calibration | wrong benchmark | separate modes; report; recalibrate |
| Adhesion calibration data | non-predictive | start with paper proxy; document domain |
| Thermal model complexity | scope creep | phase 4; empirical fallback |
| Solver-specific deletion | non-portable | templates; neutral mesh + scripts |
| G-code fallback data loss | wrong widths/roles | prefer graph; parse comments; warn |
| Overhang/droop | local error | flag; no-droop assumption; future sag model |
| License compatibility (MIT + AGPL) | distribution blocked | keep core independent; audit combined-work and dependency obligations early |
| Default profile relative-E validation | headless slice fails | minimal no-inherit process with `layer_change_gcode: "G92 E0"` |
| 2.4.2 bundles not loaded by 2.5 | preset resolution fails | self-contained process fixtures; 2.5 bundles or datadir |
| Fresh 2.5 profiles | test setup friction | checked-in fixtures; migrate old profiles |
| Fixture inherits active default profile | non-reproducible slices | check in machine/filament fixtures; pin process keys |
| Ender 3 V2 Neo variability | geometry mismatch | controlled profile; n≥3; record filament/settings |
| Measurement uncertainty | false conclusions | calibrated tools; SOP; report uncertainty |
| Overhang/bridge droop | model error | quantify with coupons; flag/refine |
| Plugin hooks GUI-only | no headless plugin CI | GUI automation; upstream CLI plugin mode; core tests stay headless |
| Nominal vs derived width | geometry mismatch | volume-conserving cross-section; report the delta |
| Sideload activation/reference | probe does not run | `.install_state.json` + `plugins` reference (`<plugin_key>;;<capability_name>`) |
| Audit prompts outside `~/.config/OrcaSlicer` | export friction | plugin storage; G-code folder; declare permissions |
| Two-build divergence (2.5 plugin vs 2.4.2 MCP) | confusion | pin builds; document roles; no plugin dependency on MCP |

## Definition of done

- **v0.1 (geometry):** Phase 0–1 complete; plugin discovery/hook smoke test on 2.5.0-beta; ROI STEP + sidecar + report; volume/envelope/watertight gates pass; docs and tests.
- **v0.2 (benchmark + physical geometry):** Phase 2 and 2b complete; paper comparison and Ender 3 V2 Neo coupon measurements documented.
- **v0.3 (adhesion):** Phase 3 complete; cohesive workflow + solver template.
- **v1.0 (plugin):** Phase 5 complete; end-to-end OrcaSlicer UX; docs; packaging.

## References

- Seibel, S., Kiendl, J. (2025). *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* Progress in Additive Manufacturing 10:6981–6992. https://doi.org/10.1007/s40964-025-01021-8. Local copy: `sources/fracture_behavior/fracture_behavior_FFF.pdf` (CC BY 4.