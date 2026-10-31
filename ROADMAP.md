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

Two OrcaSlicer builds are installed; no Flatpak remains.

**Primary plugin target:** `/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage`

- Version 2.5.0-beta build `345efec6`.
- Plugin manager verified active in the launch log: `~/.config/OrcaSlicer/orca_plugins` was created and discovery found 0 plugins; Python 3.12.13, bundled `uv`, and the audit hook are active.
- Config and logs: `~/.config/OrcaSlicer`.
- Headless CLI is still unverified: `--help` started GUI mode. Resolve this before the integration harness by trying the extracted `usr/bin/orca-slicer`, a `--slice` invocation, or the 2.5.0-beta Flatpak.
- The app currently has fresh `Default Setting` / `Default Printer` / `Default Filament` profiles. Use checked-in fixtures or migrate the old Flatpak profiles.

**Secondary MCP target:** `/home/nicholas/Applications/OrcaSlicer-MCP-2.4.2-mcp.13-linux-x64.AppImage`

- Version 2.4.2-mcp.13; includes the Remote API required by `orcaslicer-mcp`.
- It does not contain the 2.5 plugin API. Use it only for MCP-driven slicing/settings experiments.

The 2.5 build is the only plugin target (`src/slic3r/plugin` is absent at `v2.4.2` and present at `v2.5.0-beta`). The MCP server remains optional and separate.

The project-local `opencode.json` configures `orcaslicer-mcp` (`uvx orcaslicer-mcp`). It connects, but its tools return `ORCA_API_TOKEN is required` until the 2.4.2-mcp.13 AppImage is running with the Remote API enabled and the token is exported in OpenCode's environment. The stock 2.5.0-beta build does not expose the fork's Remote API, so the MCP cannot drive it.

## Phase 0 — Integration spike

**Goal:** prove the data path and toolchain on one bead.

**Deliverables**

- A probe plugin installed under `~/.config/OrcaSlicer/orca_plugins/` that runs at `posSimplifyPath` and dumps one layer's `ExtrusionPath` data (points, width, height, `mm3_per_mm`, role).
- A minimal bead sweep from that data.
- A STEP export and import into the target FEA tool.
- A headless CLI test path (or a documented GUI-only path) with fixture profiles and models.
- A written toolchain decision: OCCT/build123d vs. gmsh vs. mesh-first.

**Acceptance**

- The 2.5 plugin manager discovers the probe (`Found 1 plugin manifest`) and the hook runs on pinned build `345efec6`.
- Bead dimensions and volume match the slicer within 0.1%.
- The STEP imports, is watertight, and has the expected bounding box.
- The probe does not block slicing; outputs land in plugin storage or a permitted export folder.
- The headless test path is established, or the GUI-only workflow is documented.

**Risks**

- Plugin API churn; graph lifetime; scaled-coordinate mistakes; OCCT sweep issues.
- The 2.5 AppImage CLI entrypoint is unverified; audit prompts may appear for outputs outside `~/.config/OrcaSlicer`; the 2.5 app has fresh profiles.

## Phase 1 — Core geometry

**Goal:** a complete, standalone geometry pipeline.

**Deliverables**

- Slice-cache schema and reader/writer.
- Cross-section solver for the rounded octagon/barrel; three volume modes.
- Sweep/mesh generation for straight and curved paths; joins; caps.
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
- **Plugin testing:** unit (core), integration (2.5 CLI + fixtures), plugin discovery/smoke (2.5 GUI/logs), GUI smoke (manual).
- **Documentation:** schema, assumptions, calibration, troubleshooting.
- **Licensing:** OrcaSlicer AGPL-3.0; OCCT LGPL; gmsh GPL; dependency audit before distribution.
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
| D10 | Headless CLI path | Open | Try extracted `usr/bin/orca-slicer`, `--slice`, or the 2.5.0-beta Flatpak |
| D11 | MCP role | Resolved | Optional agent tooling for 2.4.2; not a plugin dependency |

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
| Licensing | distribution blocked | audit dependencies early |
| 2.5 AppImage CLI unverified | slow headless tests | extracted binary; `--slice`; 2.5 Flatpak |
| Fresh 2.5 profiles | test setup friction | checked-in fixtures; migrate old profiles |
| Audit prompts outside `~/.config/OrcaSlicer` | export friction | plugin storage; G-code folder; declare permissions |
| Two-build divergence (2.5 plugin vs 2.4.2 MCP) | confusion | pin builds; document roles; no plugin dependency on MCP |

## Definition of done

- **v0.1 (geometry):** Phase 0–1 complete; plugin discovery/hook smoke test on 2.5.0-beta; ROI STEP + sidecar + report; volume/envelope/watertight gates pass; docs and tests.
- **v0.2 (benchmark):** Phase 2 complete; paper comparison documented.
- **v0.3 (adhesion):** Phase 3 complete; cohesive workflow + solver template.
- **v1.0 (plugin):** Phase 5 complete; end-to-end OrcaSlicer UX; docs; packaging.

## References

- Seibel, S., Kiendl, J. (2025). *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* Progress in Additive Manufacturing 10:6981–6992. https://doi.org/10.1007/s40964-025-01021-8. Local copy: `sources/fracture_behavior/fracture_behavior_FFF.pdf` (CC BY 4.