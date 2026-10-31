# GCode-FEA OrcaSlicer plugin — user guide

**Date:** 2026-10-31
**Verified build:** OrcaSlicer 2.5.0-beta, build `345efec6` (Linux AppImage)
**Status:** Capture and geometry export verified end-to-end in the GUI. Process
data needs an exported G-code path because this build does not dispatch the
`psGCodePostProcess` seam; see [Process data](#process-data).

## What the plugin does

The plugin exposes two capabilities:

| Capability | Type | What it does |
|---|---|---|
| Capture Slice Cache | slicing pipeline | at `posSimplifyPath`, serializes every bead of the live slice into the core cache |
| Export FEA Geometry | script | reads that cache and writes sidecar, report, optional STL/STEP, contacts, interfaces, and process-informed bond quality |
| Export STEP (viewable) | script | reads the same cache and writes only a viewable planar STEP file, with a face/size preflight |

All geometry and export logic lives in the MIT-licensed `gcode_fea` core. The
adapter only captures, configures, reports, and shows progress.

## Install

### Option A: wheel (recommended; bundles the core)

```bash
python3 helper_scripts/build_plugin_wheel.py --out dist
mkdir -p ~/.config/OrcaSlicer/orca_plugins/gcode_fea
cp dist/gcode_fea_orca-0.1.0-py3-none-any.whl \
   ~/.config/OrcaSlicer/orca_plugins/gcode_fea/
```

The plugin folder must contain exactly one entry file (the `.whl`);
`.install_state.json` and the loader's `__whl_extracted__` directory are
ignored. OrcaSlicer installs the wheel's NumPy dependency with its bundled
`uv`.

Activate it with `.install_state.json` or the Plugins window:

```json
{
  "capabilities": [
    {"Capture Slice Cache": true},
    {"Export FEA Geometry": true},
    {"Export STEP (viewable)": true}
  ],
  "enabled": true,
  "installed_from": "local",
  "installed_version": "0.1.0",
  "permissions": {"events": [], "fs_read": [], "fs_readwrite": [],
                  "network_http": [], "network_socket": [],
                  "process": [], "threading": []},
  "plugin_name": "gcode_fea_orca"
}
```

Restart OrcaSlicer (or use Plugins → Reload) after installing.

### Option B: single file (development)

```bash
python3 helper_scripts/orca_plugin_sync.py \
  --src plugins/gcode_fea --name gcode_fea
```

Activate it the same way, then set the capability config `core_path` to the
repository's `src` directory so the adapter can import the core. Without the
wheel, the core is not bundled and `gcode-fea` must be importable.

## Slice and export

1. Open a project, choose the process preset, and slice the plate.
2. The slicing pipeline runs **Capture Slice Cache** at `posSimplifyPath` and
   writes:
   - `plugin_data/<plugin_key>/cache/slice.json` — the core cache,
   - `cache/slice_probe.json` — the raw probe payload and staleness fingerprint.
3. Open the **Plugins** window (File → Plugins) or press **Space** and run
   **Export FEA Geometry**, or run **Export STEP (viewable)** for a quick
   viewing file.
4. Outputs go to `plugin_data/<plugin_key>/exports/`:
   - `beads.json` — sidecar with beads, contacts, interfaces, and process data
     when a G-code path is configured,
   - `report.json` — summary, warnings, content hash,
   - optional `beads.stl`, `beads.step`.

Verified on the 20 mm cube: 390 beads, 100 layers, 3.2 MB sidecar, 1.4 KB
report. The export took a few seconds.

## Configuration

Select the capability in the Plugins window, open **Config**, and edit the
JSON. Defaults:

```json
{
  "mode": "volume_conserving",
  "c_h": 0.7,
  "c_v": 0.225,
  "resolution": 0.1,
  "step_stride": 1,
  "step_engine": "auto",
  "step_tolerance": 0.0,
  "max_step_faces": 150000,
  "roi": null,
  "layer_range": null,
  "formats": ["sidecar", "report"],
  "write_stl": false,
  "write_step": false,
  "contacts": true,
  "core_path": "",
  "cache_path": "",
  "output_dir": "",
  "gcode_path": "",
  "ask_output_dir": true
}
```

| Key | Meaning |
|---|---|
| `mode` | `volume_conserving`, `nominal_envelope`, or `paper_octagon` |
| `c_h`, `c_v` | octagon contact fractions (paper modes) |
| `resolution` | contact grid and voxel resolution (mm); 0.05–0.2 is practical |
| `step_stride` | keep every Nth profile point in STEP; 1 = full |
| `step_engine` | `auto`, `gmsh`, or `planar`; `auto` uses gmsh if importable, otherwise the built-in writer |
| `step_tolerance` | simplify curved profile sections within this chord tolerance (mm); 0 keeps every point |
| `max_step_faces` | preflight limit that stops an oversized STEP export |
| `roi` | `[xmin, ymin, zmin, xmax, ymax, zmax]` or `null` |
| `layer_range` | `[first, last]` or `null` |
| `formats` | subset of `["sidecar", "report"]` |
| `write_stl`, `write_step` | optional mesh/B-rep exports |
| `contacts` | run contact detection |
| `core_path` | `src` directory for the single-file install |
| `cache_path`, `output_dir`, `gcode_path` | overrides; blank means plugin storage |
| `ask_output_dir` | ask for an output folder in the native file dialog |

The capture capability has its own config: `mode`, `c_h`, `c_v`,
`max_layers`, `cache_path`.

**Export STEP (viewable)** has a smaller config aimed at viewing:

```json
{
  "step_stride": 1,
  "step_engine": "planar",
  "step_tolerance": 0.005,
  "max_step_faces": 150000,
  "roi": null,
  "layer_range": null,
  "core_path": "",
  "cache_path": "",
  "output_dir": "",
  "save_path": "",
  "ask_save_path": true
}
```

It writes `beads.step` where you choose, or in `exports/` when the dialog is
disabled. Defaults use the built-in planar writer, stride 1, a 0.005 mm chord
tolerance for curved sections, and the 150k-face preflight. Set `roi` or `layer_range` to keep a large part
small; raise `max_step_faces` only if you accept the file size.

## Cache management and staleness

- Capture writes `slice_probe.json` and stores its SHA-256 in the cache meta.
- Export compares the probe fingerprint with the cache. A mismatch adds
  `warning: the cache is stale; slice again` to the success message.
- Deleting `cache/slice.json` forces a fresh capture.
- `max_layers` caps large slices; the cache meta records `truncated`.
- The cache schema is shared with the CLI (`gcode-fea --cache ...`).

## STEP viewing

`write_step: true` now works without gmsh. The built-in `planar` engine writes
an AP214 `MANIFOLD_SOLID_BREP` with **one closed solid per bead**: rings at
path vertices are shared, so the internal caps and overlapping segment prisms
of the first implementation are gone. OCCT, FreeCAD, PrePoMax, and other STEP
viewers open the result. Separate beads remain separate bodies (one body per
bead) so bead/interface identity is not lost; overlapping *segments inside a
bead* are merged into its body.

Curved profile sections can be simplified with `step_tolerance` (maximum chord
deviation in mm). Measured on the Phase 0 probe (3 beads):

| Setting | Solids | Faces | File size | Volume error |
|---|---:|---:|---:|---:|
| stride 1, no tolerance | 3 | 3,052 | 2.9 MB | 0.05% |
| stride 2, no tolerance | 3 | 1,527 | 1.4 MB | 2.0% |
| stride 1, tolerance 0.005 | 3 | 734 | 0.67 MB | 1.3% |
| stride 1, tolerance 0.002 | 3 | 1,296 | — | 0.4% |

The 20 mm cube (390 beads) has about 427k faces with no simplification,
103k faces at tolerance 0.005, and 77k at tolerance 0.01. Use `roi` and/or
`layer_range` for large parts; the plugin refuses exports above
`max_step_faces` (default 150,000). For the highest-quality analytic B-rep,
install gmsh and set `step_engine: "gmsh"`; `auto` picks it when available.

## Choosing the save location

Both export capabilities ask for a location by default:

- **Export FEA Geometry** uses a native folder chooser; all outputs go there.
- **Export STEP (viewable)** uses a native Save As dialog for the `.step` file.

Set `ask_output_dir: false` or `ask_save_path: false` to skip the dialog and use
the configured/default path.

OrcaSlicer's plugin API has no file-dialog call, so the plugin asks the
desktop's standard helper in a separate process:

| Platform | Helper |
|---|---|
| Linux/GNOME | `zenity --file-selection` |
| Linux/KDE | `kdialog --getsavefilename` / `--getexistingdirectory` |
| macOS | `osascript` (`choose file name`, `choose folder`) |
| Windows | PowerShell + `System.Windows.Forms` dialogs |

The first use prompts for process permission; accept it once. If no helper is
installed (for example a minimal Linux desktop), the plugin falls back to the
configured path and says so in the result message. Cancel in the dialog aborts
the export with a “skipped” result.

Using the system helper rather than `tkinter`/Qt is intentional: OrcaSlicer's
plugin rules forbid a second GUI toolkit on the UI thread.

## Process data

The plugin keeps the exported G-code at `cache/last.gcode` when the pipeline
calls `psGCodePostProcess`. On build `345efec6` that seam was **not dispatched**
during G-code export, so process-informed bond quality needs an explicit path:

1. Export the G-code with **Export G-code file**.
2. Set `gcode_path` in the Export capability config to that file.
3. Run **Export FEA Geometry** again.

The sidecar then contains `process`, `interfaces[]` with `bond_quality`, and
`adhesion`. If no G-code is configured, the export still succeeds and records
geometry and contacts only.

Alternative: run `gcode-fea-process` on the cache with the G-code path.

## Permissions and auditing

The plugin makes no network calls. It starts the native dialog helper process
(`zenity`/`kdialog`/`osascript`/PowerShell) only when a save/folder dialog is
requested, and starts no threads.

- The wheel installs into the OrcaSlicer data directory; imports of the bundled
  core are declared up front with `orca.request_permissions(fs_read=[...])`, so
  loading asks once for the Python runtime roots and uv cache.
- Writes go to plugin storage by default. If `output_dir` points outside it,
  OrcaSlicer prompts once and remembers the answer for the plugin.
- On the first capture after a reinstall, the audit hook may also prompt for
  `FsReadWrite:open` (file access without a target). Accept once; the grant is
  stored in `.install_state.json`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Plugin not listed | folder missing, wrong entry file, or disabled | one `.whl` or `.py` per folder; enable in Plugins |
| `Local plugins required ... are missing` | preset reference does not match the plugin key | for wheels use `<wheel-stem>;;Capture Slice Cache`, e.g. `gcode_fea_orca-0.1.0-py3-none-any;;Capture Slice Cache`; for `.py` use the file stem |
| Slice button blocked | referenced capability disabled or renamed | enable it in Plugins, or fix the preset reference |
| `No slice cache yet` | export run before a capture | slice once; check the pipeline capability is enabled |
| `core is not importable` | single-file install without the core | install the wheel, or set `core_path` to the repo `src` |
| Export freezes the UI | script capabilities run on the UI thread | use ROI/layer range; STEP on large models is slow |
| STL/STEP missing | `write_stl`/`write_step` false | enable the flags |
| STEP preflight refused | face count exceeds `max_step_faces` | set an ROI/layer range, increase `step_stride`, or raise the limit |
| STEP import is slow | flat-faceted planar file is large | use ROI/layer range and `step_stride` 2–4 |
| No save dialog appears | `zenity`/`kdialog` missing, or process permission denied | install a helper, accept the process prompt, or set `ask_save_path: false` / `ask_output_dir: false` |
| Save dialog appears every run | `ask_save_path`/`ask_output_dir` true | disable in Config for a fixed path |
| Cache stale | probe changed after capture | slice again |
| PermissionError / audit block | prompt answered No | reinstall or edit `.install_state.json` permissions, then reload |
| No process data | `gcode_path` empty or seam not dispatched | export G-code and set `gcode_path` |

Logs: `~/.config/OrcaSlicer/log/python_*.log` (tracebacks) and
`debug_*.log` (load and audit messages).

## Licensing

- Adapter and core: MIT. The wheel bundles `LICENSE` and `CREDITS.md`.
- OrcaSlicer: AGPL-3.0. The plugin uses only the public `orca` Python API; no
  OrcaSlicer source is copied into the plugin or the core.
- gmsh is GPL-2.0-or-later and is **not** bundled; STEP export requires it in
  the plugin environment, or use the CLI with `uv run --with gmsh`.
- The paper is CC BY 4.0; see `CREDITS.md`.

## Verification status

| Check | Status |
|---|---|
| Wheel builds, metadata, RECORD hashes, import | Pass (unit tests) |
| Plugin discovered and loaded by 2.5.0-beta | Pass (log: 2 capabilities, no load error) |
| Capture at `posSimplifyPath` | Pass (390 beads, 100 layers) |
| Script export from the Plugins/Speed Dial | Pass (sidecar + report) |
| Permissions declared once | Pass (combined load prompt; first-capture `open` prompts) |
| Planar STEP output read by OCCT/gmsh | Pass (3 solids, correct volume, bbox; one body per bead) |
| One body per bead, no overlapping segment prisms | Pass (3 beads → 3 solids) |
| Curved-section tolerance | Pass (3,052 → 734 faces at 0.005 mm, 1.3% volume) |
| Planar STEP from the plugin | Pass (unit test; no gmsh needed) |
| “Export STEP (viewable)” capability | Pass (unit test; writes `beads.step`) |
| Native save/folder dialog integration | Unit-tested for zenity, kdialog, osascript, PowerShell; live zenity not exercised in this session |
| `psGCodePostProcess` capture | Not dispatched in this build; explicit `gcode_path` used instead |
| End-to-end on Windows/macOS | Not tested |

## Limitations

- The export runs on the UI thread; very large ROI/STEP exports block the app.
- Planar STEP files are flat-faceted and can be large; use ROI/layer range.
- Different beads are exported as separate bodies, not one unioned solid.
  Fusing them would require a boolean kernel; the planar writer intentionally
  preserves bead identity.
- Beads with near-180° reversals (some zigzag infill) fall back to per-segment
  solids because an unbounded miter would be invalid. Perimeters and normal
  raster lines are one body.
- There is no Pages tab or docked panel yet.
- The cache stores one object per slice; multi-plate/multi-object aggregation is
  future work.
- Process data depends on a G-code file path on this build.
- Supports, multi-material, and per-path flow are not exposed by the graph.
