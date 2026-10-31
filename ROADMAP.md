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

**Status (2026-10-31): core MVP complete.** The core ingests Phase 0/Phase 1 probes and caches, solves cross-sections, meshes beads, detects contacts, filters ROI, and writes sidecar/report/STL/STEP. See `docs/phase1_report.md`.

**Deliverables**

- Slice-cache schema and reader/writer. **Done.**
- Cross-section solver for the rounded octagon/barrel; three volume modes. **Done.**
- Sweep/mesh generation for straight and curved paths; joins; caps. **Done** as a segment-prism compound; optional miter sweep.
- gmsh/OCCT-backed STEP/mesh prototype behind an export interface. **Done** for STL and polygonal-profile STEP.
- Contact detection: intralayer and interlayer; contact area, normal, centroid, gap/overlap. **Done** with a 2.5D footprint grid.
- ROI: object, bbox, layer range, mesh volume. **Partially done** (bbox/layer range; mesh-volume ROI pending).
- STEP export: faceted and analytic; bead compound, fused, glued, envelope. **Partial:** bead compound of segment prisms; fused/glued/envelope pending.
- Optional volume mesh export with element sets. **Not done**.
- Sidecar and report. **Done**, with a deterministic content hash.
- Unit/integration tests and small fixtures. **Done:** 41 tests, real 20 mm cube fixture.

**Acceptance**

- 20 mm cube: **pass** (390 beads, 100 layers).
- Thin-wall: the cube has a two-wall perimeter; a dedicated thin-wall fixture is pending.
- Overhang/bridge: the cube contains an internal-bridge role; the no-droop assumption is documented, and a dedicated overhang coupon is pending.
- Multi-material: **pending**; the model/report support labels, but the plugin graph does not expose per-path material.
- Volume within 1%: **pass** (`0.05%` mesh vs analytic).
- Envelope within one line width: **pass** (`3.4 µm` bbox deviation).
- >99.9% watertight: **pass** (100% of beads; compounds, not fused).
- Deterministic IDs and report: **pass** (content hash stable across runs).
- Performance: 390 beads/100 layers run in seconds; no formal budget yet. STEP size is the binding constraint.

**Risks**

- Bead count; boolean cost; self-intersections; sparse-infill contact complexity; file size.
- Segment prisms are a compound with internal caps; fusing is downstream.
- 2.5D contacts aggregate closed-loop pairs and do not split contact patches.
- STEP uses a polygonal profile and grows quickly; ROI/stride are required.
- Multi-material, supports, and per-path process data remain unavailable from the plugin graph.
- Volume-mesh export is not implemented.

## Phase 2 — Paper benchmark

**Goal:** reproduce Seibel & Kiendl's unidirectional PLA results.

**Status (2026-10-31): simulation tooling complete; solver run pending.** Coupon generation, voxel meshing, Abaqus/CalculiX input writing, deletion/localization, and the comparison harness are implemented and tested. No solver is installed in this environment, so the strength trend and `E`/`σ_f` comparison are not yet demonstrated. See `docs/paper_benchmark.md`.

**Deliverables**

- Paper octagon mode with `c_h`, `c_v`, area scaling. **Done.**
- Material card: `E = 3901 MPa`, `ν = 0.3`, `σ_f = 45.38 MPa`. **Done.**
- Rankine criterion. **Done**, with von Mises for comparison.
- Boundary conditions and load steps for the 0/30/45/60/90° coupons. **Done** as a recipe plus solver node sets.
- Element-deletion + localization script for the target solver. **Done** as a solver-agnostic driver; not yet coupled to a live solver.
- Comparison report: `E`, `σ_f`, failure mode, angle trend. **Harness done**; results pending.

**Acceptance**

- The strength trend decreases with angle; intralayer failure for non-0°; 0° behaves differently. **Pending solver run.**
- `E` and `σ_f` within the agreed tolerance (target ~10–15%). **Pending solver run.**
- The script runs to complete failure on the reduced model. **Driver tested on synthetic fields; solver coupling pending.**
- `docs/paper_benchmark.md` records the comparison. **Written with status and pending items.**

**Risks**

- Calibration mismatch between the paper's reduced-area model and the volume-conserving model.
- Mesh convergence and runtime.
- Solver-specific element deletion/localization.
- Voxel staircasing: interface area is overestimated until `c_v b` is resolved.

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

**Status (2026-10-31): interface tooling complete; solver run pending.** Interface extraction with contact-area correction, cohesive cards and bilinear laws, mesh splitting with zero-thickness COH3D8 elements, the Abaqus cohesive deck, and the sensitivity matrix are implemented and tested. The card is an explicitly labelled starting estimate. CalculiX cohesive input and the solver validation remain open. See `docs/cohesive_interfaces.md`.

**Deliverables**

- Interface extraction from contacts: surfaces/elements, normals, areas. **Done**, with `area_model` and mesh faces.
- Cohesive material cards: `σ_n`, `σ_s`, `G_Ic`, `G_IIc`, stiffness, damage evolution. **Done**; initial card is uncalibrated.
- Solver templates: Abaqus and CalculiX (at least one end-to-end). **Abaqus done**; CalculiX pending.
- Calibration workflow: peel/shear/DCB or paper-derived starting values. **Plan done**; physical tests not run.
- Sensitivity study: interface strength/toughness vs. macroscopic strength. **Law-level matrix done**; solver sweep pending.
- Comparison to the geometric proxy. **Structural comparison documented**; strength comparison pending a solver run.

**Acceptance**

- A coupon simulation develops a discrete crack at the expected interface. **Pending solver run.**
- Mesh convergence at the interface. **Pending.**
- The solver template runs from a clean checkout with documented commands. **Generation passes; solver step pending.**
- The predicted trend is consistent with the paper/experiments. **Pending.**

**Risks**

- Interface mesh complexity; solver support; calibration data; mode mixity; runtime.
- Cohesive element count and junction nodes; area scaling depends on the 2.5D contact model.

## Phase 4 — Process-informed adhesion

**Goal:** make interface properties spatially varying and predictive.

**Status (2026-10-31): tooling complete; calibration and physical validation pending.** G-code process parsing, per-bead process records, the thermal/healing bond-quality model, sidecar integration, and the uncertainty envelope are implemented and tested. No coupons were printed and no model parameter is calibrated. See `docs/process_adhesion.md`.

**Deliverables**

- Per-path process data: speed, temperature, fan, layer time (G-code parsing or new binding). **Done** via G-code; 100% bead coverage on the 20 mm cube.
- Thermal/healing model: interface temperature history → bond degree → `σ`/`G`. **Done** as a lumped Arrhenius model, flagged uncalibrated.
- Per-interface bond-quality field in the sidecar. **Done**, with process and adhesion sections.
- Experimental validation at different layer times/temperatures/fan settings. **Pending** (physical tests not run).
- Uncertainty report. **Done** as a Monte Carlo sensitivity envelope.

**Acceptance**

- Predicted strength trend matches experiments better than a global `c_v`. **Pending physical validation.**
- The model reports uncertainty and its calibration domain. **Pass** (`uncertainty.json`).
- The workflow is documented and reproducible. **Pass** for generation; calibration pending.

**Risks**

- Thermal model complexity; missing sensors/calibration; material variability; runtime.
- Lumped cooling saturates under default parameters, narrowing the interlayer quality range.
- G-code timing ignores acceleration and firmware limits.

## Phase 5 — Plugin UX and packaging

**Goal:** a usable OrcaSlicer experience.

**Status (2026-10-31): complete for the Linux 2.5.0-beta build.** Wheel and single-file installs, capture + export capabilities, config, progress/cancel, native save/folder dialogs with staged copying for arbitrary folders, cache staleness, permissions, a portable planar STEP writer with per-bead merging and chord-tolerance simplification, and the user guide are implemented. A GUI smoke test on build `345efec6` loaded the wheel, captured 390 beads/100 layers, and exported the sidecar/report without a terminal. The Pages tab is not implemented. See `docs/plugin_user_guide.md`.

**Deliverables**

- Script capabilities: "Export FEA Geometry" (sidecar/report/STL/STEP) and "Export STEP (viewable)" (cache-only STEP), both with progress, cancel, and native save/folder dialogs. **Done.** STEP works without gmsh via the planar B-rep writer; overlapping segments are stitched into one body per bead, `step_tolerance` reduces curved-surface polygons, and `step_engine=merged` boolean-unions the per-segment prisms into merged bodies (Manifold3D; validated with SolidCraft interference on a 5-layer cube); `trim_overlaps` is an optional clipping fallback.
- Pages tab (optional): slice status, export history, report viewer. **Not done**; plugin storage and the report JSON cover status.
- Config: defaults, ROI, modes, output formats, solver. **Done**; solver choice is covered by the benchmark/cohesive CLIs, not the plugin.
- Cache management and stale-slice detection. **Done** via `slice_hash` and `source_hash`.
- Install/packaging: `.py` or `.whl`; dependencies; permissions. **Done**; the wheel bundles the core and NumPy is the only dependency.
- Include `LICENSE` and `CREDITS.md`; document the MIT/AGPL boundary for the bundled plugin. **Done.**
- Docs: user guide, troubleshooting, examples. **Done.**

**Acceptance**

- A user can slice, export, and open the result without a terminal. **Pass** on build `345efec6` (Plugins window / Speed Dial).
- The export does not block the UI or silently freeze slicing. **Partial**: the script runs on the UI thread with progress checkpoints; large STEP exports still block. Capture is on the slicing thread.
- Errors are actionable and logged. **Pass**; failures return `ExecutionResult.failure` and tracebacks go to `python_*.log`.
- The plugin declares only the permissions it needs. **Pass**: no network/process/threads; reads declared at load; writes default to plugin storage and prompt once for external paths.

**Risks**

- Experimental API; UI thread rules; audit prompts; dependency size.
- The wheel preset reference includes the wheel stem (and thus the version); document and re-point presets after version bumps.
- `psGCodePostProcess` was not dispatched during G-code export on build `345efec6`; process data needs an explicit `gcode_path` or the CLI.
- The first capture after a reinstall may require accepting `FsReadWrite:open` prompts once.

## Phase 6 — STEP/export hardening and cleanup

**Goal:** close the small correctness and UX gaps left after Phase 5 without changing the core contracts or the benchmark schedule.

**Status (2026-10-31): in progress.** STEP `closed`/`open_edges`/`bytes`/`dropped_solids` reporting, closed-shell failure for dropped merged solids, `max_step_triangles` forwarding, and the read-only `step_audit.py`, `step_budget.py`, and `plugin_permission_audit.py` helpers are in place. Planar shell switching, coplanar merging, tiny-face cleanup, full `cube.stl` plugin coverage, and optional-dependency CI remain.

**Deliverables**

- **Planar STEP correctness.** The planar writer still unions stitched/mitred bead solids. Sharp closed-loop corners can self-intersect, which produced an open shell and thin fragments in SolidCraft with the earlier merged engine. Either switch the planar writer to per-segment prisms or validate each shell and fall back to segment prisms. Report `closed`/`open_edges` for every planar export.
- **Merged STEP quality.** Replace the zero-area-triangle nudge with proper degenerate/sliver cleanup, and reduce the triangle B-rep size by merging coplanar triangles or decimating without opening the shell. Report triangle, face, and byte counts; keep the SolidCraft interference check at zero.
- **Tiny-face cleanup.** Eliminate the remaining unmeshable tiny faces SolidCraft reports on the 5-layer cube (56 faces at last check), or document the exact tessellation limit and filter them before writing.
- **Full-part ergonomics.** When the merged export exceeds `max_step_triangles`, report the estimated layer/ROI budget and suggest a concrete `layer_range`/ROI. Report the number of disjoint components; add an explicit warning when a part is not one body because of tangent-only contacts.
- **`cube.stl` end-to-end coverage.** Generate a full 10 mm cube probe/cache, export through the installed plugin's STEP capability (not only the core), and run SolidCraft interference. Add the probe/cache as a fixture or a reproducible helper script.
- **Retire the clipping fallback.** Remove `trim_overlaps` from default/plugin docs and eventually from the code; keep `step_engine=merged` as the only overlap-resolution path. Avoid two competing behaviors in the UI.
- **Dependency fallback.** Make missing `manifold3d`/`shapely` produce an actionable plugin result (install command or plugin-wheel guidance) instead of a traceback. Add a startup capability/config check.
- **Versioned wheel key.** Add a migration helper or documented procedure for preset references after a wheel version bump, and add a regression test for the local key. Track upstream OrcaSlicer issue `#17100`.
- **Process-data fallback.** On builds where `psGCodePostProcess` is not dispatched (`345efec6`), make the missing process data explicit and offer the exported-G-code path in the same result, rather than silently exporting geometry only.
- **Optional-dependency CI.** Run the merged/trim test groups in a job that installs the optional extras, so the currently skipped tests are exercised in CI.

**Acceptance**

- Every STEP export reports `closed: true` or fails with a clear reason; `open_edges == 0`.
- SolidCraft interference is `0` on the 5-layer cube and on the `cube.stl` end-to-end export.
- No unmeshable faces in the SolidCraft import, or a documented, reproducible exception.
- A full-part export that exceeds the triangle budget produces an actionable ROI/layer-range recommendation.
- The plugin STEP capability is exercised on `tests/fixtures/models/cube.stl` from a clean checkout, not only through core unit tests.
- `trim_overlaps` is no longer a default or recommended path.
- Missing optional dependencies and versioned preset keys produce clear, tested errors or migrations.

**Risks**

- Coplanar merging can be slower and numerically delicate; validate with SolidCraft and OCCT.
- Full `cube.stl` STEP may still be large; use a reduced layer range for the regression.
- Upstream plugin keys and audit behavior may change between beta builds; pin and document.

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
| D16 | Sweep join model | Resolved | Segment-prism compound default; miter sweep optional (volume loss at corners) |
| D17 | Contact model | Resolved | 2.5D footprint grid; contact-patch splitting is future work |
| D18 | Benchmark solver target | Open | Abaqus and CalculiX input writers implemented; no solver installed yet |
| D19 | Benchmark mesh | Resolved | Voxel hex8 mesh; 0.05 mm or finer to resolve the paper strand |
| D20 | Benchmark failure criterion | Resolved | Rankine for brittle PLA; von Mises only for comparison |
| D21 | Cohesive area source | Resolved | Contact model area; voxel faces scaled by A_contact/A_face |
| D22 | Cohesive solver target | Resolved | Abaqus COH3D8 end-to-end; CalculiX pending |
| D23 | Cohesive card values | Open | Initial estimate only; calibrate with peel/shear/DCB |
| D24 | Process data source | Resolved | G-code parsing; plugin graph lacks per-path process data |
| D25 | Bond-quality model | Open | Lumped two-time cooling + Arrhenius healing; parameters uncalibrated |
| D26 | Probe/G-code frame alignment | Resolved | Auto-estimate XY plate offset from bounding boxes |
| D27 | Plugin packaging | Resolved | Self-contained wheel bundles the core; single-file `.py` for development |
| D28 | Plugin cache flow | Resolved | Capture at `posSimplifyPath`; script export reads the cache |
| D29 | Plugin progress model | Resolved | UI-thread script + progress checkpoints; no background thread in the adapter |
| D30 | Planar STEP join model | Open | Validate mitred shells or switch to per-segment prisms; reject open shells |
| D31 | Merged STEP simplification | Open | Coplanar merge or decimation; keep the shell closed |
| D32 | Full-part STP budget UX | Open | Recommend ROI/layer range from the triangle estimate |
| D33 | Clipping fallback | Resolved | Deprecate `trim_overlaps`; merged union is the supported overlap path |

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
| Segment compound internal caps | not a single fused solid | fuse downstream; document |
| 2.5D contact approximation | aggregated/closed-loop contacts | finer grid; patch splitting; validate |
| STEP size (polygonal profile) | large files | ROI; stride with error check; fused/faceted later |
| Multi-material data gap | no per-path material | add plugin binding/G-code; report default |
| Volume mesh absent | manual FEA meshing step | voxel hex8 export implemented |
| Voxel interface area overestimate | cohesive stiffness too high | scale interface areas from contacts; refine voxels |
| No solver available locally | benchmark cannot be executed | ship generated jobs; run on a solver host |
| Uncalibrated cohesive card | non-predictive interface strength | flag `calibrated=false`; run calibration plan |
| Cohesive junction elements | distorted elements at multi-bead nodes | inspect mesh; refine or tie junction nodes |
| CalculiX cohesive gap | workaround scripts | Abaqus is the claimed target; add CalculiX later |
| Uncalibrated adhesion model | non-predictive bond quality | flag `calibrated=false`; report uncertainty and domain |
| G-code time underestimation | wrong thermal history | report lower-bound caveat; calibrate against measured layer times |
| Interlayer quality saturation | weak sensitivity | revisit cooling parameters during calibration |
| Wheel key includes version | preset references break on upgrade | document the reference; re-point presets after bumps |
| UI-thread export | freeze on large STEP | ROI/layer range; document; move to a worker later |
| `psGCodePostProcess` not dispatched | no automatic process data | explicit `gcode_path`; revisit on newer builds |
| Planar STEP size | hundreds of MB for full parts | ROI/layer range; `step_stride`; `step_tolerance`; `max_step_faces` preflight |
| Mitred planar shells | open bodies/fragments at sharp corners | validate closed shells; fall back to segment prisms |
| Merged triangle B-rep size | slow imports and large files | coplanar merge/decimation; `max_step_triangles` and ROI guidance |
| Tiny degenerate faces | SolidCraft unmeshed faces | filter slivers before STEP; regression check |
| Full `cube.stl` gap | plugin path untested on the fixture | add full probe/cache and GUI STEP smoke |
| Optional dependency install | merged/trim export fails | actionable dependency check; CI extras |
| Separate bead bodies | not one unioned solid | per-bead merge is intentional for interface identity; a boolean kernel would be needed for full fusion |
| Audit prompts outside `~/.config/OrcaSlicer` | export friction | plugin storage; G-code folder; declare permissions |
| Two-build divergence (2.5 plugin vs 2.4.2 MCP) | confusion | pin builds; document roles; no plugin dependency on MCP |

## Definition of done

- **v0.1 (geometry):** Phase 0 and Phase 1 core complete; plugin discovery/hook smoke test; real 20 mm cube fixture; ROI STEP + sidecar + report; volume/envelope/watertight gates pass; dedicated thin-wall/overhang/multi-material fixtures pending; docs and tests.
- **v0.2 (benchmark + physical geometry):** Phase 2 tooling complete (solver run pending) and Phase 2b complete; paper comparison and Ender 3 V2 Neo coupon measurements documented.
- **v0.3 (adhesion):** Phase 3 and Phase 4 tooling complete; cohesive workflow + Abaqus template + process-informed bond quality (solver run, calibration, and physical validation pending).
- **v1.0 (plugin):** Phase 5 complete; end-to-end OrcaSlicer UX verified on Linux 2.5.0-beta build `345efec6`; docs and wheel packaging; Pages tab and process-seam capture pending.
- **v1.1 (hardening):** Phase 6 complete; planar/merged STEP shells validated closed, SolidCraft interference zero on the test cube, full `cube.stl` plugin coverage, optional-dependency CI, and retirement of the clipping fallback.

## References

- Seibel, S., Kiendl, J. (2025). *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* Progress in Additive Manufacturing 10:6981–6992. https://doi.org/10.1007/s40964-025-01021-8. Local copy: `sources/fracture_behavior/fracture_behavior_FFF.pdf` (CC BY 4.