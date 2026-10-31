# GCode-FEA OrcaSlicer plugin

This directory contains the OrcaSlicer adapter. It captures the live sliced bead
graph and exports FEA geometry through the `gcode_fea` core.

- `gcode_fea_plugin.py` — the single-file plugin (capture + two export capabilities).
- Install with the self-contained wheel:

  ```bash
  python3 helper_scripts/build_plugin_wheel.py --out dist
  mkdir -p ~/.config/OrcaSlicer/orca_plugins/gcode_fea
  cp dist/gcode_fea_orca-0.0.1-py3-none-any.whl \
     ~/.config/OrcaSlicer/orca_plugins/gcode_fea/
  ```

- Or side-load the single file for development:

  ```bash
  python3 helper_scripts/orca_plugin_sync.py \
    --src plugins/gcode_fea --name gcode_fea
  ```

  Then set `core_path` to the repository `src` directory in the capability
  config.

Full instructions: `docs/plugin_user_guide.md`.

Release history: [CHANGELOG.md](../../CHANGELOG.md).

The plugin is MIT-licensed. OrcaSlicer is AGPL-3.0; this adapter only uses the
public `orca` API and copies no OrcaSlicer source.
