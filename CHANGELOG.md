# Changelog

All notable changes to GCode-FEA are recorded here. Versions follow
[Semantic Versioning](https://semver.org/).

Each release has one entry with the version, date, and a short summary of
user-visible changes. The summary is written in plain text so it can also be
pasted into the Orca Cloud plugin **Changelog** field, which OrcaSlicer shows as
a Version / Date / Changes table.

## 0.1.0 - 2026-10-31

Initial release.

### Added

- OrcaSlicer 2.5 plugin with three capabilities: **Capture Slice Cache**,
  **Export FEA Geometry**, and **Export STEP (viewable)**.
- Core library that rebuilds the as-deposited bead mesostructure from a sliced
  print: rounded cross-sections, volume-conserving sweeps, contracts and
  interfaces, region-of-interest filtering, and deterministic bead IDs.
- Export formats: JSON sidecar, JSON report, binary STL, and STEP.
- STEP engines: pure-Python planar B-rep, gmsh/OCCT, and Manifold3D merged
  bodies with closed-shell reporting.
- Contact and cohesive-interface extraction, including COH3D8 decks and
  cohesive cards for Abaqus.
- Process-informed adhesion from G-code: per-bead process records, a lumped
  Arrhenius bond-quality model, and an uncertainty envelope.
- Analytic benchmark coupons and voxel hex8 meshes with Abaqus/CalculiX input
  writers.
- Plugin UX: native save/folder dialogs, staged exports, progress and cancel,
  cache staleness checks, config, and safe permission declarations.
- Read-only helpers for STEP auditing/budgeting and plugin permission checks.
- A release helper that builds the plugin wheel and publishes it to GitHub
  Releases from the matching changelog entry.
- A nightly GitHub Actions workflow that runs the unit tests on every push to
  `main` and, when they pass, updates a rolling prerelease wheel.
- Documentation, test fixtures, and SolidCraft screenshots of exported STEP
  files.

### Compatibility

- OrcaSlicer **2.5.0-beta build `345efec6`** (the plugin API is experimental).
- Python **3.12 or newer**.
- Optional extras: `gmsh` for the OCCT STEP engine, `manifold3d` for merged STEP,
  `shapely` for the legacy wall-overlap trimming fallback.

### Known limitations

- No finite element solver run is included or recorded yet; benchmark and
  cohesive jobs produce solver input files only.
- No physical print validation is included yet.
- Merged STEP exports can contain a few tiny faces that some importers cannot
  mesh; this is tracked for a future release.
- The OrcaSlicer Changelog tab is populated from Orca Cloud releases. A locally
  side-loaded plugin has no changelog metadata and shows "No changelog
  available"; this file is the project record.

### Orca Cloud release note

Initial release: OrcaSlicer capture/export plugin, bead mesostructure core,
STEP/STL/sidecar/report export, contacts and cohesive interfaces, and
process-informed adhesion tooling. No solver or physical validation yet.
