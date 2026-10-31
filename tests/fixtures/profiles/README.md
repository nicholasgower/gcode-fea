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

Pass `--logfile out/orca.log` (or run from a temporary directory) so the CLI
does not leave a `00000.log` in the repository root.

Verified command:

```bash
mkdir -p out
/home/nicholas/Applications/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage \
  --appimage-extract-and-run \
  --slice 0 --outputdir out --logfile out/orca.log \
  --load-settings tests/fixtures/profiles/process_headless.json \
  tests/fixtures/models/cube.stl
```
