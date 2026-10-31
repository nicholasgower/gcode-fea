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

Phase 0 integration spike complete (2026-10-31). The probe plugin, the GUI slicing hook, and the minimal one-bead STEP path work; see `docs/phase0_report.md`. The core geometry pipeline is not implemented yet. This document records the modeling assumptions, integration facts, and engineering rules that the implementation must follow. The phased plan is in `ROADMAP.md`.

Open decisions are tracked in `ROADMAP.md`; do not silently resolve them in code.

## 3. Repository layout

Current:

```text
AGENTS.md, ROADMAP.md, CREDITS.md, LICENSE
sources/fracture_behavior/   # Seibel & Kiendl (2025) paper and license
src/gcode_fea/               # package stubs
plugins/phase0_probe/        # Phase 0 slicing-pipeline probe
helper_scripts/              # env doctor, slice wrapper, plugin sync, Phase 0 bead builder
tests/                       # helper tests and fixtures
tests/fixtures/phase0/       # real probe JSON + minimal bead STEP/report
docs/phase0_report.md        # Phase 0 results and findings
```

Planned:

```text
src/gcode_fea/               # core library: bead model, cross-sections, contacts, export
src/gcode_fea/orca/          # thin OrcaSlicer plugin adapter
src/gcode_fea/solvers/       # Abaqus/CalculiX templates and element-deletion helpers
plugins/phase0_probe/        # Phase 0 slicing-pipeline probe plugin
helper_scripts/              # env doctor, headless slice wrapper, plugin sync
tests/                       # unit, integration, benchmark, regression
tests/fixtures/profiles/     # machine/process/filament JSON for headless 2.5 CLI
tests/fixtures/models/       # cube, thin wall, overhang, multi-material
tests/fixtures/plugin_dummy/ # minimal plugin fixture for sync-helper tests
tests/fixtures/slices/       # serialized bead caches for core-only tests
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

The slicer's `width` is a target line width; `mm3_per_mm` is the deposited area. They are not the same quantity. In the Phase 0 probe, `width * height` was about 10% larger than `mm3_per_mm`. Always derive the cross-section from `mm3_per_mm` and report the difference from `width`; do not assume `A = width * height`.

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

**Verified local environment (2026-10-31).** No OrcaSlicer Flatpak remains. Two AppImages are installed:

**Primary plugin target:** `/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage`

- Version 2.5.0-beta build `345efec6`.
- Embedded Python 3.12.13 with a bundled `uv`.
- Plugin manager verified active in the launch log: `~/.config/OrcaSlicer/orca_plugins` was created and discovery found 0 plugins.
- Config and logs: `~/.config/OrcaSlicer`; plugin stderr: `~/.config/OrcaSlicer/log/python_*.log`.
- Headless CLI verified: use the `--appimage-extract-and-run` flag. The `APPIMAGE_EXTRACT_AND_RUN=1` environment-variable path started GUI mode and did not pass arguments. See section 15 for the commands.
- Headless slicing verified with a minimal no-inherit process override containing `"layer_change_gcode": "G92 E0"`. The default profile fails relative-extruder validation until that override is applied.

**Secondary MCP target:** `/home/nicholas/Applications/OrcaSlicer-MCP-2.4.2-mcp.13-linux-x64.AppImage`

- Version 2.4.2-mcp.13; includes the Remote API required by `orcaslicer-mcp`.
- It does not contain the 2.5 Python plugin API. Use it only for MCP-driven slicing/settings experiments.
- `--help` and `--info` were verified with `APPIMAGE_EXTRACT_AND_RUN=1` for this 2.4.2 build only; do not generalize that form to 2.5.

**MCP tooling.** The project-local `opencode.json` configures the `orcaslicer` MCP server (`uvx orcaslicer-mcp`). It connects, but its tools require the 2.4.2-mcp.13 fork's Remote API token. Launch that AppImage, enable **Preferences → Remote API**, copy the token, and set `ORCA_API_TOKEN` in the environment that launches OpenCode. `ORCA_API_URL` defaults to `http://127.0.0.1:13130`. The stock 2.5.0-beta build does not expose the fork's Remote API, so the MCP server cannot drive it; the plugin does not depend on the MCP.

**Reference hardware for physical tests.** The printer is a Creality Ender 3 V2 Neo:

- 220 × 220 × 250 mm build volume.
- 0.4 mm nozzle, 1.75 mm filament, Bowden extruder.
- Marlin 2.0.8.3 on a 4.2.2 board, CR Touch, max nozzle 250–260 °C, bed 100 °C.
- Practical layer height 0.1–0.4 mm; typical speed ~50 mm/s, max 120 mm/s.
- PLA/PETG/ABS supported; use PLA for the paper benchmark.

All physical validation coupons must fit this bed, use the Ender 3 V2 Neo OrcaSlicer profile, and be sliced for Marlin. Record the exact filament, nozzle, temperatures, fan, speed, flow, and layer height with every test. Check the machine/process/filament profiles into `tests/fixtures/profiles/` when the physical test campaign starts. Do not use a generic printer profile for physical validation. The paper used a Raise3D Pro2 (0.4 mm, 0.2 mm layers, PLA, 200/60 °C, 30 mm/s), so its `c_h`/`c_v` and material data are starting points, not universal constants.

Plugin API facts for the pinned build (upstream documentation; no plugin hook has run in this environment yet — only plugin-manager startup has been observed):

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
- The 2.5 audit hook allows writes under `~/.config/OrcaSlicer`; use `orca.host.plugin.storage()` for caches and the G-code folder or a user-chosen path for exports. Other paths may prompt once.
- Install the plugin under `~/.config/OrcaSlicer/orca_plugins/<plugin>/`. The loader scans at startup; reload or restart after adding or changing a plugin.
- Pin the 2.5.0-beta build `345efec6`; the plugin API is experimental.

**Phase 0 observed behavior (2026-10-31).**

- The 2.5 headless CLI (`--slice`) does **not** initialize the Python plugin manager. Plugin hooks run in GUI mode only. Use the CLI for core/G-code tests and the GUI for plugin-hook tests.
- A preset selects a pipeline plugin with both `slicing_pipeline_plugin` and the dynamic `plugins` array. The reference is `<plugin_key>;;<capability_name>` (for example `phase0_probe;;Phase0 Probe`), not the display name.
- A sideloaded plugin must be activated: `.install_state.json` with `enabled: true` and the capability enabled, or the Plugins dialog. Discovery alone is not enough.
- The probe wrote `~/.config/OrcaSlicer/orca_plugins/plugin_data/phase0_probe/phase0_probe_obj102.json`; the hook ran at `posSimplifyPath` and slicing completed.
- Slicer nominal width can differ from the volume-derived width (9.4% on the default flow). Conserve `mm3_per_mm` and report the delta.
- gmsh/OCCT is the Phase 0/Phase 1 prototype toolchain. `build123d` was attempted but its dependency tree exceeded available disk space; gmsh is much lighter.

Architecture consequences:

1. Capture and serialize the bead model at `posSimplifyPath` into a compact cache. Do not build STEP there.
2. Export from the cache on demand (script/pages command) or at the end of the pipeline.
3. Keep the core standalone; the plugin adapter is thin.
4. For process-informed adhesion, parse G-code for per-path process data or add a binding to the G-code generator. Document the gap.
5. For supports, parse G-code or add a binding for `support_fills`. Document the gap.
6. Develop and test the plugin on the 2.5.0-beta build. The 2.4.2 MCP fork is a separate application and is not a plugin test target.

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
- Tests: unit tests for cross-sections and contacts; integration tests for small slices; plugin discovery/hook smoke tests in 2.5; benchmark tests for the paper; regression tests for golden volumes/hashes.
- OrcaSlicer paths: config/plugins/logs under `~/.config/OrcaSlicer`; plugin target 2.5.0-beta build `345efec6`; MCP target 2.4.2-mcp.13.
- Reference printer: Creality Ender 3 V2 Neo. Use its OrcaSlicer profile for physical tests and record the exact print settings with each coupon.
- License: MIT for original project code (`LICENSE`); preserve `CREDITS.md`. The paper is CC BY 4.0; OrcaSlicer and the MaxEllis fork are AGPL-3.0. Do not copy AGPL code into the MIT core; review combined-work obligations before distributing a bundled plugin or modified OrcaSlicer. When the Python project skeleton is created, declare `license = { text = "MIT" }` in `pyproject.toml`.
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
8. **Plugin discovery:** the 2.5 plugin manager finds the plugin (`Found 1 plugin manifest`) and the `posSimplifyPath` hook runs on pinned build `345efec6`.
9. **Permissions:** outputs go to plugin storage or a permitted export folder; any audit prompt is expected and documented.
10. **Physical geometry:** on the Ender 3 V2 Neo, coupon mass/volume, outer dimensions, and cross-section/contact measurements are within the agreed tolerances; deviations and their likely causes are reported.

## 14. Agent do / don't

Do:

- Keep the core independent of OrcaSlicer.
- Use per-path values (`height`, `width`, `mm3_per_mm`, `role`) rather than global settings.
- Preserve volume by default and report the mode.
- Make every approximation explicit in the report.
- Keep bead and interface IDs stable and deterministic.
- Add tests with every geometry or schema change.
- Pin/verify the OrcaSlicer version before relying on a binding: 2.5.0-beta build `345efec6` for the plugin; 2.4.2-mcp.13 fork for the MCP.
- Test the plugin in the 2.5 app, not the 2.4.2 MCP fork.
- Record the Ender 3 V2 Neo profile and exact print settings with every physical coupon.
- Keep `LICENSE` and `CREDITS.md` current when adding code or third-party material.

Don't:

- Don't hardcode material properties, `c_v`, or printer settings.
- Don't use a generic printer profile for physical validation.
- Don't treat the paper's `c_v` or material data as universal; recalibrate on the Ender 3 V2 Neo.
- Don't copy AGPL-licensed OrcaSlicer code into the MIT core without reviewing license compatibility.
- Don't default to a whole-part bead B-rep.
- Don't silently union beads and lose interface identity.
- Don't claim the paper benchmark passes until the comparison is in `docs/`.
- Don't import `orca` in the core or in tests that should run headless.
- Don't rely on experimental plugin behavior without an adapter and a version check.

## 15. Commands

Project commands are targets; update them when the project skeleton exists. The OrcaSlicer 2.5 helper commands below are verified.

Canonical OrcaSlicer 2.5 headless path (verified 2026-10-31):

```bash
python3 helper_scripts/orca_env.py --json
python3 helper_scripts/orca_slice.py --dry-run
python3 helper_scripts/orca_slice.py --out out --verify
python3 helper_scripts/orca_plugin_sync.py --dry-run --src tests/fixtures/plugin_dummy --name dummy
python3 -m unittest discover -s tests -p "test_*.py" -v
```

`orca_slice.py` always passes `--appimage-extract-and-run`, `--slice 0`, `--outputdir`, and `--logfile`, and removes any inherited `APPIMAGE_EXTRACT_AND_RUN`. It renames the single generated G-code to the model stem. Expected artifacts: `out/cube.gcode`, `out/orca.log`, `out/result.json`.

`tests/fixtures/models/cube.stl` is a 10 mm smoke-test cube. Phase 2b's physical coupons use a 20 mm cube.

Raw command (for debugging only; the helper is canonical):

```bash
/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage \
  --appimage-extract-and-run \
  --slice 0 --outputdir out --logfile out/orca.log \
  --load-settings tests/fixtures/profiles/process_headless.json \
  tests/fixtures/models/cube.stl
```

Minimal process override (self-contained; no `inherits`, because 2.4.2 system bundles are not loaded by 2.5):

```json
{
  "type": "process",
  "name": "Headless",
  "from": "user",
  "layer_change_gcode": "G92 E0",
  "compatible_printers": []
}
```

Phase 0 probe (GUI; verified 2026-10-31):

```bash
python3 helper_scripts/orca_plugin_sync.py \
  --src plugins/phase0_probe --name phase0_probe
# Activate the sideloaded plugin with .install_state.json or the Plugins dialog.

# Build a 3MF carrying the plugin reference and pipeline setting.
/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage \
  --appimage-extract-and-run \
  --export-3mf /tmp/opencode/phase0.3mf \
  --load-settings tests/fixtures/profiles/process_phase0.json \
  tests/fixtures/models/cube.stl
```

Open the 3MF in the GUI, acknowledge **Customized Preset**, and click
**Slice plate**. The 2.5 CLI does not initialize plugins. The hook writes
`~/.config/OrcaSlicer/orca_plugins/plugin_data/phase0_probe/phase0_probe_obj*.json`.
Then build and verify the bead:

```bash
uv run --with gmsh python helper_scripts/phase0_bead.py
```

Or reproduce from the checked-in Phase 0 probe without rerunning the GUI:

```bash
uv run --with gmsh python helper_scripts/phase0_bead.py \
  --probe tests/fixtures/phase0/phase0_probe_obj102.json \
  --step tests/fixtures/phase0/phase0_bead.step \
  --report tests/fixtures/phase0/phase0_bead.report.json
```

The default run writes `out/phase0_bead.step` and
`out/phase0_bead.report.json`; the fixture command rewrites the checked-in
`tests/fixtures/phase0/` copies. See `docs/phase0_report.md` for the full
results and acceptance checklist.

Project commands (planned):

```bash
uv sync
uv run pytest
uv run gcode-fea export --gcode examples/cube.gcode --out out/cube.step
uv run gcode-fea export --slice-cache out/cube.beads.json --mode volume-conserving --roi 10,10,0,30,30,20
uv run gcode-fea validate out/cube.report.json
```

## 16. References

- Seibel, S., Kiendl, J. (2025). *A finite element approach for modelling the fracture behaviour of unidirectional FFF-printed parts.* Progress in Additive Manufacturing 10:6981–6992. https://doi.org/10.1007/s40964-025-01021-8. Local copy: `sources/fracture_behavior/fracture_behavior_FFF.pdf` (CC BY 4.0).
- Attribution and credits: `CREDITS.md` (project MIT license, paper citation, CC BY 4.0 notice, changes made, software credits).
- OrcaSlicer plugin system: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_system.html
- OrcaSlicer plugin development: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_development.html
- OrcaSlicer slicing API: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/slicing.html
- OrcaSlicer host API: https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/host.html
- OrcaSlicer source: `src/slic3r/plugin/host/PluginHostSlicing.cpp`, `src/libslic3r/Print.cpp`, `src/libslic3r/ExtrusionEntity.hpp`, `src/libslic3r/Layer.hpp`.
- Tooling candidates: OpenCASCADE (OCCT), build123d/cadquery, gmsh, trimesh/manifold3d, meshio.
