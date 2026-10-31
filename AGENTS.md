# AGENTS.md — GCode-FEA

## 1. What this project is

GCode-FEA is an OrcaSlicer plugin plus a standalone core library that exports the **as-deposited bead mesostructure** of a sliced FFF print in an FEA-friendly form.

The primary command is `Export`. It produces:

- a STEP file of the sliced print geometry,
- a JSON sidecar describing beads, contacts, interfaces, process data, and provenance,
- optionally, a volume mesh with element sets for a target solver,
- a report of volume, void fraction, contact statistics, warnings, and performance.

The output is not the nominal CAD surface. It is the geometry the printer actually deposits: individual beads, inter-bead voids, layer stacking, and strand orientation. That is the level of detail needed to study failure modes such as intralayer debonding and layer-adhesion-driven anisotropy.

## 2. Status

Design phase. There is no implementation code yet. This document records the modeling assumptions, integration facts, and engineering rules that the implementation must follow. The phased plan is in `ROADMAP.md`.

Open decisions are tracked in `ROADMAP.md`; do not silently resolve them in code.

## 3. Repository layout

Current:

```text
sources/fracture_behavior/   # Seibel & Kiendl (2025) paper and license
```

Planned:

```text
src/gcode_fea/               # core library: bead model, cross-sections, contacts, export
src/gcode_fea/orca/          # thin OrcaSlicer plugin adapter
src/gcode_fea/solvers/       # Abaqus/CalculiX templates and element-deletion helpers
tests/                       # unit, integration, benchmark, regression
docs/                        # design notes, schema, validation reports
examples/                    # small G-code/slice fixtures and expected outputs
```

The core must not import `orca`. The adapter depends on the core; the core does not depend on OrcaSlicer.

## 4. Product contract

`Export` inputs:

- the current/last sliced print, or a serialized slice cache, or a G-code file (fallback),
- region of interest: whole plate, selected objects, bounding box, layer range, or mesh volume,
- representation: bead compound, fused layers, faceted STEP, analytic STEP, envelope, volume mesh,
- cross-section model and contact fractions,
- volume mode,
- include/exclude: perimeters, infill, supports, skirt, brim, raft, purge tower, first layer,
- material/interface model,
- output paths, units, coordinate frame, tessellation tolerance.

`Export` outputs:

- `*.step` (or `.stp`),
- `*.beads.json` sidecar,
- optional `*.inp` / `*.msh` volume mesh,
- `*.report.json` and a human-readable summary,
- a stable exit code and machine-readable errors.

The command must be deterministic: the same slice cache and config produce the same bead IDs and the same geometry (within numerical tolerance).

## 5. Non-negotiable modeling assumptions

1. **100% deposition.** Every commanded extrusion volume is deposited. No ooze, stringing, under-extrusion, or purge loss. Retractions and pressure advance affect the bead ends, not the total volume; ignore or taper them in Phase 1.
2. **Volume is the primary invariant.** The deposited area per unit length is `mm3_per_mm`. The cross-section must reproduce that area unless the user explicitly selects the paper's reduced-area mode.
3. **Beads are swept cross-sections.** Each extrusion path becomes a solid swept along its polyline. The cross-section is perpendicular to the path tangent; the build direction defines "up."
4. **No droop in Phase 1.** Unsupported beads have a flat bottom at the layer plane. Flag bridges and overhangs; do not silently pretend the geometry is accurate there.
5. **As-printed, not nominal.** The outer surface is the envelope of bead arcs. It will be scalloped and may differ from the CAD surface. Export a smoothed nominal envelope separately if needed for contact/BCs.
6. **Supports are excluded by default.** They are usually removed. If included, they are separate bodies with contact/no-bond, not part of the part.
7. **Approximations are reported.** Every simplification (no droop, mitred joins, clipped ROI, faceted arcs, merged beads, excluded supports) appears in the report.

## 6. Bead cross-section model

Use a rounded-octagon / barrel family. Parameters:

- `h` — bead height (from `ExtrusionPath.height` or `Layer.height`),
- `w_t` — flat top/bottom width (interlayer contact),
- `h_s` — flat side height (intralayer contact),
- `s` — side bulge (sagitta); total width `w = w_t + 2s`,
- `R` — side arc radius.

For a circular side arc through the top and bottom corners of the flat, separated by `h`:

```latex
R = \frac{s}{2} + \frac{h^2}{8s}
```

```latex
A = w_t h + 2\left[R^2\arccos\!\left(1-\frac{s}{R}\right) - (R-s)\sqrt{2Rs-s^2}\right]
```

Given `A`, `h`, and one shape constraint, solve for the remaining unknown numerically.

Degenerate cases:

| Shape | `h_s` | `s` | Notes |
|---|---:|---:|---|
| Rectangle | `h` | `0` | no arcs |
| Capsule / stadium | `0` | `h/2` | flat top/bottom, semicircular sides |
| Paper octagon | `c_v h` | chamfer | straight corner cuts |
| Rounded octagon | `c_v h` | solved | general case |

Arc direction:

- `s > 0`: convex, over-filled relative to the flat rectangle.
- `s = 0`: straight sides.
- `s < 0`: concave, under-filled.
- `s >= h/2`: more than a semicircle; flag as an undercut and consider clipping.

If the requested area cannot be represented (e.g., too small for the chosen `w_t` and `h`), return a structured warning and a fallback cross-section; do not silently produce invalid geometry.

## 7. Volume modes

| Mode | Fixed inputs | Volume solves | Volume exact? | Use |
|---|---|---|---|---|
| Nominal envelope | `w`, `h` | contact width `w_t`, bulge | yes | outer surface, CAD comparison |
| Volume-conserving | `w_t`, `h` | bulge `s`, total `w` | yes | predictive mesostructure (default) |
| Paper octagon | `w`, `h`, `c_h`, `c_v` | nothing; area reduced | no; `E`/`σ` scaled | reproducing Seibel & Kiendl |

The paper's octagon is not volume-conserving. With `c_h = 0.7`, `c_v = 0.2`:

```latex
A_{\text{oct}} = a b\left[1-\frac{1}{2}(1-c_h)(1-c_v)\right],
\qquad
\frac{A_{\text{rect}}}{A_{\text{oct}}} = 1.136
```

The paper scales `E` and `σ_f` by 1.136. Do not mix that calibration with the volume-conserving mode. Report which mode was used and the resulting bead width/overlap.

## 8. OrcaSlicer integration

As of the 2.5-era plugin API (verify against the pinned OrcaSlicer version):

**Verified local environment (2026-10-31).** The installed Flatpak is `com.orcaslicer.OrcaSlicer` 2.4.2 (Flathub stable, GNOME 50 runtime). Its headless CLI runs (`--help`, `--info`, `--slice`) and can slice to G-code. However, the `v2.4.2` source tag does not contain `src/slic3r/plugin`; the Python plugin API first appears in `v2.5.0-alpha`. The local Flatpak therefore cannot load the slicing-pipeline plugin. For 2.4.2, use the standalone core + G-code/CLI path. The slicing-pipeline adapter requires a 2.5.0-alpha/beta build (AppImage or source build), not the current Flathub stable.

- Plugins are Python packages (`.py` with PEP 723 metadata, or `.whl`) registering capabilities.
- Available capability bases: script, pages, slicing-pipeline, printer-agent, resource-texture. A first-class `Exporter` type is reserved but not implemented.
- A slicing-pipeline capability at a late geometry step such as `posSimplifyPath` receives `ctx.print` / `ctx.object`, the live slicing graph.
- The graph exposes `Print` → `PrintObject` → `Layer` → `LayerRegion` → `perimeters` / `fills` → `ExtrusionPath`.
- `ExtrusionPath` exposes `points` (scaled 3D), `width`, `height`, `mm3_per_mm`, `role`.
- Graph references are valid only during `execute(ctx)`. Do not stash them.
- The hook runs on the slicing worker thread.
- `psGCodePostProcess` has only `gcode_path` and `output_name`; `ctx.print` is `None`.
- Script capabilities run on the UI thread and can read `orca.host.model()` (the mesh), but not the sliced graph.
- `SupportLayer.support_fills` is not exposed by the current bindings.
- Per-path speed, temperature, and fan are not exposed by `ExtrusionPath`.

Architecture consequences:

1. Capture and serialize the bead model at `posSimplifyPath` into a compact cache. Do not build STEP there.
2. Export from the cache on demand (script/pages command) or at the end of the pipeline.
3. Keep the core standalone; the plugin adapter is thin.
4. For process-informed adhesion, parse G-code for per-path process data or add a binding to the G-code generator. Document the gap.
5. For supports, parse G-code or add a binding for `support_fills`. Document the gap.

Audit/permissions: the plugin runs under a permission system. Declare filesystem read/write targets and any process/thread use with `orca.request_permissions(...)`; prefer writing to the plugin's own storage or the G-code folder.

## 9. Data model / sidecar

The STEP is geometry; the sidecar is the analysis data. Minimum schema:

- `meta`: schema version, plugin version, OrcaSlicer version, slice hash, config hash, timestamp, units, coordinate frame.
- `print`: objects, transforms, plate, relevant resolved config.
- `beads`: `id`, object id, layer index, `print_z`, `height`, `role`, material/tool, path points (mm, printer frame), `width`, `mm3_per_mm`, cross-section parameters (`w_t`, `h_s`, `s`, `R`, mode), `volume`, tangent, bounding box.
- `contacts`: bead A, bead B, type (intralayer/interlayer), area, normal, centroid, gap/overlap.
- `interfaces`: `id`, type, area, normal, neighboring beads, process data (if available), optional bond-quality scalar.
- `warnings`: under-filled beads, gaps, overlaps, clipped regions, unsupported features, simplifications.
- `outputs`: STEP, mesh, report paths.
- `provenance`: git commit, dependency versions, command line.

Stable IDs: derive from object id + layer index + path index + segment index, with a deterministic hash of the source slice. Do not use memory addresses or random UUIDs.

## 10. FEA and adhesion

The paper is the validation anchor:

- Octagon strands, `a = 0.4 mm`, `b = 0.2 mm`.
- Measured `c_h = 0.72 ± 0.032`, `c_v = 0.36 ± 0.066`.
- Initial model `c_h = 0.7`, `c_v = 0.2`; calibrated `c_v = 0.225`.
- `E_sim = 3901 MPa`, `ν = 0.3`, `σ_f,sim = 45.38 MPa`.
- Rankine max-normal-stress criterion is better than von Mises for brittle PLA.
- Element deletion is more robust than erosion for complete failure.
- Localization: delete only the top ~0.01% of elements exceeding the criterion per step.
- Turnarounds/edges contribute ~5%; smooth edges are acceptable.
- ~500k elements for a 10 × 50 mm patch; ~24 h on 96 processors.

Critical caveat: the paper's `c_v` reduction encodes a material/process effect in the geometry. It is a calibrated proxy, not a predictive adhesion model. Keep **contact area** (geometry) and **bond quality** (process/material) separate in the data model.

Adhesion phases:

1. Perfect bond (upper bound).
2. Paper geometric proxy (validation).
3. Cohesive zone interfaces with a solver template.
4. Process-informed cohesive properties from thermal history.

Do not hardcode material properties or `c_v`. They are calibration inputs, not constants.

## 11. Output representations and scale

| Representation | What it is | Use |
|---|---|---|
| Bead compound | one solid per bead, grouped | interface-preserving FEA |
| Fused layer | one solid per layer/part | viewing, volume checks |
| Glued solid | union with shared faces | interface face sets |
| Faceted B-rep | STEP from a mesh | large models, robust export |
| Envelope | outer hull only | contact/BCs, CAD comparison |
| Volume mesh | hex/tet with sets | analysis-ready |

Default to region of interest for bead-resolved exports. Full-part bead B-rep is not a supported default. Use coarse/homogenized modes or submodeling for full parts.

"FEA-friendly" means:

- watertight, manifold solids,
- no degenerate faces or self-intersections (or a clear report),
- named bodies/sets per bead, layer, role, material, interface,
- shared or explicit interfaces at contacts,
- consistent units and coordinate frame,
- reasonable file size and meshing cost,
- a reproducible material/BC/solver recipe.

## 12. Engineering conventions

- Units: mm, MPa, N, s. Angles in radians internally, degrees at the UI boundary.
- Coordinate frame: printer frame, Z up, mm. Apply `PrintObject.trafo()` to object-local paths. Provide an optional Y-up transform for solvers that expect it.
- Layer z: `Layer.print_z` is the top of the layer; a bead spans `print_z - height` to `print_z`. Verify against the pinned version.
- Path points: scaled integers; convert with the plugin's unscale helper or the scaling factor.
- Numbers: float64 for geometry, float32 only at the mesh boundary.
- Determinism: stable IDs, sorted iteration, no unordered-map order in outputs.
- Errors: structured codes + human-readable messages; never silently drop a bead.
- Performance: measure bead count, triangles, file size, and wall time. Set and update budgets in `ROADMAP.md`.
- Tests: unit tests for cross-sections and contacts; integration tests for small slices; benchmark tests for the paper; regression tests for golden volumes/hashes.
- Style: Python 3.12+, type hints, `ruff`/`black` (or the project's chosen formatter), `pytest`. Keep the core free of GUI and OrcaSlicer imports.

## 13. Validation gates

A change is not done until:

1. **Volume:** sum of bead volumes matches the slicer's deposited volume within 1% (excluding explicitly excluded structures).
2. **Envelope:** the outer envelope is within about one line width of the nominal surface, except flagged overhang/bridge/seam regions.
3. **Geometry:** >99.9% of solids are watertight/manifold; any failures are reported with bead IDs.
4. **STEP import:** the target solver imports the file; volume and named sets survive.
5. **Benchmark:** the paper's 0/30/45/60/90° cases reproduce the strength trend and failure mode within the agreed tolerance.
6. **Performance:** the ROI export meets the current budget.
7. **Reproducibility:** the same slice + config produces the same report hash (or stable IDs and volume table).

## 14. Agent do / don't

Do:

- Keep the core independent of OrcaSlicer.
- Use per-path values (`height`, `width`, `mm3_per_mm`, `role`) rather than global settings.
- Preserve volume by default and report the mode.
- Make every approximation explicit in the report.
- Keep bead and interface IDs stable and deterministic.
- Add tests with every geometry or schema change.
- Pin/verify the OrcaSlicer version before relying on a binding.

Don't:

- Don't hardcode material properties, `c_v`, or printer settings.
- Don't default to a whole-part bead B-rep.
- Don't silently union beads and lose interface identity.
- Don't claim the paper benchmark passes until the comparison is in `docs/`.
- Don't import `orca` in the core or in tests that should run headless.
- Don't rely on experimental plugin behavior without an adapter and a version check.

## 15. Commands (planned)

These are targets; update them when the project skeleton exists.

```bash
uv sync
uv run pytest
uv run gcode-fea export --gcode examples/cube.gcode --out out/cube.step
uv run gcode-fea export --slice-cache out/cube.beads.json --mode volume-conserving --roi 10,10,0,30,30,20
uv run gcode-fea validate out/cube.report.json
```

## 16. References

- Seibel, S., Kiendl, J. (2025). *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* Progress in Additive Manufacturing 10:6981–6992. https://doi.org/10.1007/s40964-025-01021-8. Local copy: `sources/fracture_behavior/fracture_behavior_FFF.pdf` (CC BY 4.0).
- OrcaSlicer plugin system: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_system.html
- OrcaSlicer plugin development: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_development.html
- OrcaSlicer slicing API: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/slicing.html
- OrcaSlicer host API: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/host.html
- OrcaSlicer source: `src/slic3r/plugin/host/PluginHostSlicing.cpp`, `src/libslic3r/Print.cpp`, `src/libslic3r/ExtrusionEntity.hpp`, `src/libslic3r/Layer.hpp`.
- Tooling candidates: OpenCASCADE (OCCT), build123d/cadquery, gmsh, trimesh/manifold3d, meshio.
