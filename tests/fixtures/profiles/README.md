# Headless process fixture

`process_headless.json` is a self-contained process override for OrcaSlicer 2.5
headless CLI tests. Verified with 2.5.0-beta build `345efec6`.

- It does **not** use `inherits`. The 2.5 AppImage does not load the old 2.4.2
  JSON system bundles, so inheriting a 2.4.2 process fails with
  "Preset was not found in the loaded bundle".
- It sets `layer_change_gcode` to `G92 E0`. OrcaSlicer's relative-extruder
  validation rejects a slice without a per-layer extruder reset.
- `compatible_printers` is empty so the fixture works with the app's current
  default printer.

The process is applied over OrcaSlicer's active default process; missing keys
inherit from that default. The machine and filament also remain the app's
current defaults unless `--load-settings` / `--load-filaments` provide them.

## Fixture model

`tests/fixtures/models/cube.stl` is a **10 mm cube** (0–10 mm) used as a headless
smoke test. Phase 2b physical coupons use a 20 mm cube.

## Reproducibility caveat

Because the process fixture inherits missing keys from the active default
profile, a slice is not machine-reproducible until the machine and filament
fixtures are checked in and passed with `--load-settings` / `--load-filaments`.
The helper scripts currently use the app's active default machine and filament.

Pass `--logfile out/orca.log` (or run from a temporary directory) so the CLI
does not leave a `00000.log` in the repository root.

Preferred helper (renames the generated G-code to `out/cube.gcode` and verifies
the layer count):

```bash
python3 helper_scripts/orca_slice.py --out out --verify
```

Verified raw command:

```bash
mkdir -p out
/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage \
  --appimage-extract-and-run \
  --slice 0 --outputdir out --logfile out/orca.log \
  --load-settings tests/fixtures/profiles/process_headless.json \
  tests/fixtures/models/cube.stl
```
