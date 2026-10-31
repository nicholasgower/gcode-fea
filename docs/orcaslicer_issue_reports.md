# OrcaSlicer plugin-system issue reports

Submitted reports (all titles prefixed with `[2.5.0 beta]`):

- https://github.com/OrcaSlicer/OrcaSlicer/issues/17100
- https://github.com/OrcaSlicer/OrcaSlicer/issues/17101
- https://github.com/OrcaSlicer/OrcaSlicer/issues/17102
- https://github.com/OrcaSlicer/OrcaSlicer/issues/17103

Environment for all reports:

- OrcaSlicer **2.5.0-beta build `345efec6`**, Linux AppImage
  (`OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage`)
- Linux Mint 22 (Ubuntu 24.04 base), kernel 6.8, X11
- Plugin: local wheel `gcode_fea_orca-0.1.0-py3-none-any.whl` (MIT test exporter)
- Logs: `~/.config/OrcaSlicer/log/debug_*.log`

## 1. Local wheel plugin key includes the version, breaking presets on upgrade

**Steps**

1. Install a local wheel plugin:

   ```bash
   mkdir -p ~/.config/OrcaSlicer/orca_plugins/gcode_fea
   cp gcode_fea_orca-0.1.0-py3-none-any.whl \
      ~/.config/OrcaSlicer/orca_plugins/gcode_fea/
   ```

2. Enable it in the Plugins window (or `.install_state.json`).
3. Create a process preset that uses its pipeline capability and save a 3MF:

   ```json
   {
     "plugins": ["gcode_fea_orca-0.1.0-py3-none-any;;Capture Slice Cache"],
     "slicing_pipeline_plugin": ["Capture Slice Cache"]
   }
   ```

4. Check the log:

   ```text
   Discovered plugin: gcode_fea_orca (version: 0.1.0)
   Collected 3 registered capability class(es) for key
     /home/.../gcode_fea/gcode_fea_orca-0.1.0-py3-none-any.whl
   [plugin_loader::load] SUCCESS plugin=gcode_fea_orca-0.1.0-py3-none-any
   ```

5. Replace the wheel with `gcode_fea_orca-0.1.1-py3-none-any.whl` and restart.
6. Open the same 3MF. The preset reference no longer resolves; the missing-plugin
   notification appears and Slice is blocked.

**Actual**

The local plugin key is the wheel file stem, including its version:
`gcode_fea_orca-0.1.0-py3-none-any`. Upgrading the wheel changes the key, so every
preset reference to the old key becomes missing.

Source confirms this at `src/slic3r/plugin/PluginDescriptor.hpp`:
`make_local_plugin_key(stem)` uses the filename stem.

**Expected**

A stable plugin key independent of the wheel filename version (for example the
wheel's distribution or entry-package name), so upgrading a local wheel does not
invalidate presets. If the versioned key is intentional, the preset reference
should at least be migrated automatically.

**Workaround**

Update every preset `plugins` entry after each wheel version change.

## 2. “Activate Now” does not activate an installed pipeline plugin

**Steps**

1. Install `plugins/phase0_probe` as a local plugin; it is discovered:

   ```text
   Discovered plugin: Phase 0 Probe (version: 0.0.1)
   ```

2. Remove or disable its `.install_state.json` (the state was lost after
   OrcaSlicer recreated its configuration file).
3. Open a 3MF whose process preset uses
   `"plugins": ["phase0_probe;;Phase0 Probe"]`.
4. OrcaSlicer shows:

   ```text
   Plugins required by the current preset are not activated:
   - Phase0 Probe
   Activate Now
   ```

5. Click **Activate Now**.

**Actual**

The notification remains, **Slice plate** stays disabled, and the debug log shows
no `[plugin_loader::load] START plugin=phase0_probe` after the click. The plugin
is discovered but never loaded or enabled. Tested with an X11 window manager
(Metacity), not headless.

**Expected**

Clicking **Activate Now** loads/enables the installed plugin (or opens the
Plugins window for it) and clears the missing-plugin block.

**Workaround**

Create `.install_state.json` manually with `enabled: true` and the capability
enabled, then restart.

## 3. `OrcaSlicer.conf` becomes unparseable after abnormal termination

**Steps**

1. Launch the AppImage.
2. While it is starting or running, terminate it with `SIGTERM`/`SIGKILL`
   (`pkill -x orcaslicer_main`).
3. Repeat several times, including launches while another instance is still
   running.
4. Start OrcaSlicer again.

**Actual**

A dialog appears:

```text
The OrcaSlicer configuration file may be corrupted and cannot be parsed.
OrcaSlicer has attempted to recreate the configuration file.
Please note, application settings will be lost, but printer profiles will not
be affected.
```

`~/.config/OrcaSlicer/OrcaSlicer.conf` is recreated and application settings are
lost.

**Expected**

Configuration writes should be atomic (write a temporary file and rename), or a
backup should be restored, so an abnormal termination does not leave an
unparseable file. The same applies when a second instance is launched.

**Caveat**

This was observed during automated GUI testing with repeated forced terminations.
It was not reproduced with a normal **File → Quit**, and the exact write window
is not yet isolated. The report is for the robustness gap; logs and the dialog
text are exact.

## 4. Plugin API cannot pre-authorize a directory chosen at runtime

**Steps**

1. Install a script plugin that exports files (sidecar, report, STEP).
2. Use `orca.host.ui` to ask the user for an output folder (there is no native
   file-dialog API, so the plugin uses `zenity`/`kdialog`/`osascript`/PowerShell
   in a child process).
3. Export to that folder.

**Actual**

The audit hook denies or prompts for each file operation outside the plugin data
directory. The first write can be denied while the prompt is being shown
(off-thread prompts fail closed). `request_permissions(fs_readwrite=[...])` only
pre-approves exact target strings and cannot know the runtime-chosen path.

**Expected**

A supported way to do one of:

- let the plugin ask the host for a native save/folder dialog and return the
  chosen path with permission for that path, or
- let `request_permissions` / the audit hook grant a directory prefix chosen at
  runtime, or
- provide a scoped write permission for the current export operation.

**Workaround**

Write into plugin storage (an allowed root), then copy the finished files to the
chosen location with a fixed child-process helper (`sh -c cp` / `cmd /c copy`).
The process spawn asks once; the helper’s own writes are not audited. This works
but relies on the documented child-process limitation, not a plugin API.

**Related**

- #17090 “trust this plugin” (prompt fatigue)
- #16105 socket permission prompt hang
- Plugin audit docs: exact target matching and off-thread fail-closed behavior
