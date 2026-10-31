# Phase 0 — Integration spike report

**Date:** 2026-10-31
**OrcaSlicer:** 2.5.0-beta, build `345efec6` (Linux x86_64 AppImage)
**Reference printer for physical validation:** Creality Ender 3 V2 Neo.
Phase 0 used the app's default 200 × 200 × 100 printer preset for the geometry spike; the plugin/geometry path is printer-agnostic. The Ender 3 V2 Neo profile is used for Phase 2b physical tests.
**Status:** Complete, with one documented limitation: plugin hooks are GUI-only in this build.

## Objective

Prove the data path from a real OrcaSlicer slice to a bead STEP file:

- install a slicing-pipeline probe plugin;
- confirm the plugin manager discovers and loads it;
- run the probe at `posSimplifyPath`;
- dump one layer's `ExtrusionPath` data;
- build one bead from that data;
- export STEP and verify volume, bounding box, and watertightness.

## What ran

1. Installed the probe with the sync helper:

   ```bash
   python3 helper_scripts/orca_plugin_sync.py \
     --src plugins/phase0_probe --name phase0_probe
   ```

2. Enabled the side-loaded plugin with
   `~/.config/OrcaSlicer/orca_plugins/phase0_probe/.install_state.json`
   (`enabled: true`, capability `Phase0 Probe` enabled). A fresh sideload is
   discovered but not activated until this sidecar exists or the plugin is
   activated in the Plugins dialog.

3. Built a 3MF that carries the Phase 0 process preset, including both the
   `plugins` reference and the `slicing_pipeline_plugin` setting:

   ```bash
   /home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage \
     --appimage-extract-and-run \
     --export-3mf /tmp/opencode/phase0.3mf \
     --load-settings tests/fixtures/profiles/process_phase0.json \
     tests/fixtures/models/cube.stl
   ```

   The relevant preset entries are:

   ```json
   "plugins": ["phase0_probe;;Phase0 Probe"],
   "slicing_pipeline_plugin": ["Phase0 Probe"]
   ```

4. Opened the 3MF in the GUI on an Xvfb display with Metacity and clicked
   **Slice plate** via `xdotool`. The GUI is required: the 2.5 headless CLI
   (`--slice`) does **not** initialize the Python plugin manager, so the
   slicing-pipeline hook never runs in CLI mode.

5. Ran the minimal bead builder:

   ```bash
   uv run --with gmsh python helper_scripts/phase0_bead.py
   ```

## Results

### Plugin discovery and hook

The launch log contains:

```text
Discovered plugin: Phase 0 Probe (version: 0.0.1)
Plugin discovery completed in 0ms. Found 1 plugin manifests
Auto-loading plugin on startup: phase0_probe
[load_plugin_impl] SUCCESS plugin=phase0_probe
Slicing pipeline plugin 'Phase0 Probe' [posSimplifyPath]: phase0 probe wrote
  /home/nicholas/.config/OrcaSlicer/orca_plugins/plugin_data/phase0_probe/phase0_probe_obj102.json
```

The probe JSON contains:

- `orcaslicer_version`: `2.5.0-beta`
- `object_id`: `102`
- `layer_print_z`: `0.2`
- `layer_slice_z`: `0.1`
- `layer_height`: `0.2`
- `path_count`: `3`
- roles: `Inner wall`, `Outer wall`, `Bottom surface`

### Bead geometry

`helper_scripts/phase0_bead.py` selected the longest straight segment:

- path 2 (`Bottom surface`), segment 26
- length: `11.430060 mm`
- layer height: `0.200000 mm`
- deposited area `mm3_per_mm`: `0.082798608 mm²`
- volume-conserving rectangle width: `0.413993 mm`
- nominal slicer width: `0.456913 mm`
- nominal-width delta: `-9.394%`

The rectangle is volume-conserving, not a match to the slicer's nominal
extrusion width. The slicer's `width` is the target line width; the deposited
area comes from `mm3_per_mm`, and the difference is the flow/overlap model.
Phase 1's rounded-octagon cross-section must reconcile these two.

### STEP verification

`out/phase0_bead.step` was written by gmsh/OCCT and read back in a fresh gmsh
model:

- solids found: `1`
- expected volume: `0.946393061 mm³`
- STEP volume: `0.946393061 mm³`
- relative volume error: `8.4e-13`
- bounding-box maximum absolute error: `1.0e-7 mm`
- result: **PASS**

The STEP round-trip uses gmsh/OCCT. No commercial FEA solver is installed on
this machine yet, so the "target solver import" check is pending.

## Findings

1. **Plugin hooks are GUI-only in 2.5.0-beta.** The CLI slice path logs
   `cli mode` and never initializes the Python plugin manager. The same slice
   in the GUI discovers, loads, and runs the plugin. Core geometry tests can
   stay headless; plugin-hook tests need the GUI or a future CLI plugin mode.

2. **The `plugins` reference is required and uses the plugin key.** The
   pipeline hook reads both `slicing_pipeline_plugin` and the dynamic `plugins`
   array. The entry format is `<plugin_key>;;<capability_name>`, e.g.
   `phase0_probe;;Phase0 Probe`. The display name (`Phase 0 Probe`) does not
   resolve.

3. **A sideloaded plugin must be activated.** Discovery alone is not enough.
   A `.install_state.json` sidecar with `enabled: true` (or the Plugins dialog)
   is required before the capability can run. The plugin manager installs
   declared dependencies (`numpy`) with the bundled `uv`.

4. **The nominal and volume-conserving widths differ.** `mm3_per_mm` was
   `0.0827986 mm²` while `width * height` was `0.0913827 mm²` (about 90.6%).
   The bead model must conserve deposited volume and report the nominal-width
   delta; blindly using the slicer width would overstate volume by ~10%.

5. **The path point z is the layer-bottom z in this probe.** The first layer's
   path points have `z = 0.0`, while `layer_print_z = 0.2` and
   `layer_height = 0.2`. The bead spans `print_z - height` to `print_z`.
   Confirm this convention on a higher layer before Phase 1.

6. **gmsh/OCCT is a workable Phase 0 toolchain.** It created a valid STEP,
   read it back, and produced exact volume/bbox checks. `build123d` was
   attempted first but its dependency tree (OCP, threejs, scipy, scikit-learn)
   exceeded the available disk space during installation; gmsh is much lighter
   and already provides the OCC kernel and STEP writer.

7. **Phase 0 used the app's default printer preset, not the Ender profile.**
   The bead math is printer-agnostic, but this means Phase 0 is not an
   Ender-specific calibration. Phase 2b will use the Ender 3 V2 Neo profile
   and record the exact print settings.

## Acceptance checklist

- [x] Plugin manager discovers the probe (`Found 1 plugin manifests`).
- [x] Plugin loads and runs at `posSimplifyPath`.
- [x] Probe writes one layer of `ExtrusionPath` data to plugin storage.
- [x] Minimal bead built from the probe data.
- [x] STEP written and read back as one solid.
- [x] Volume matches the deposited volume (`8.4e-13` relative error).
- [x] Bounding box matches the constructed bead (`1e-7 mm` maximum error).
- [x] Slicing completes; the probe does not block the pipeline.
- [ ] STEP import into a commercial FEA solver (pending solver installation).
- [x] Headless core path documented; plugin-hook path documented as GUI-only.

## Reproduction notes

- The 3MF's `Customized Preset` dialog must be acknowledged before slicing.
- The GUI slice was automated on `DISPLAY=:99` with `metacity` and `xdotool`;
  a human can do the same from the normal desktop.
- The probe output is outside the repo:
  `~/.config/OrcaSlicer/orca_plugins/plugin_data/phase0_probe/`.
- `out/` is gitignored; the STEP and report are local artifacts.

## Toolchain decision for Phase 1

Use **gmsh/OCCT** for the first core implementation:

- it is lightweight enough to install and run here;
- it provides OCC B-rep construction, STEP read/write, and meshing;
- it handled the one-bead round-trip with exact volume and bbox checks.

Keep the export layer behind an interface so a later `build123d`/OCCT or
mesh-first implementation can replace it. Re-evaluate once the cross-section
and contact-detection workload is known.

**Licensing:** gmsh is GPL-2.0-or-later. Treat it as a development/prototype
dependency only; the MIT release path should use an LGPL OCCT binding or a
permissively licensed mesher. See `CREDITS.md`.

## Next steps

1. Implement the volume-conserving rounded-octagon cross-section and compare
   the derived width with the slicer's nominal width.
2. Add contact detection and the sidecar schema.
3. Verify the path-z convention on a higher layer.
4. Choose and install a target FEA solver; repeat the STEP import check there.
5. Decide whether to automate the GUI plugin-hook test or add a CLI plugin
   mode upstream.
