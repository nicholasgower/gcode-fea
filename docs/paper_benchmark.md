# Phase 2 — Paper benchmark (simulation tooling)

**Date:** 2026-10-31
**Reference:** Seibel & Kiendl (2025), *A finite element approach for modelling
the fracture behaviour of unidirectional FFF-printed parts*, Progress in
Additive Manufacturing 10:6981–6992.
**Status:** Tooling complete; the finite element solve itself is pending because
no solver is installed in this environment. No physical tests were performed.

## Scope

Phase 2 reproduces the paper's 0/30/45/60/90° unidirectional PLA tensile
benchmark. Deliverables implemented here:

- octagon cross-section with `c_h`/`c_v` and the `A_rect / A_oct` area scale,
- material card (`E = 3901 MPa`, `ν = 0.3`, `σ_f = 45.38 MPa`),
- Rankine and von Mises equivalent stresses,
- coupon geometry at every angle, boundary/load recipe,
- voxel hex8 mesh with bead/layer/role element sets and interface patches,
- Abaqus and CalculiX input writers,
- element-deletion localization driver,
- comparison against the published Table 2 values.

The benchmark *result* (strength trend and failure mode) cannot be claimed
until the generated jobs are run in a solver; see "Validation status".

## Paper setup reproduced

| Item | Value |
|---|---|
| Strand width `a` | 0.4 mm |
| Strand height `b` | 0.2 mm |
| Initial `c_h` | 0.7 |
| Initial `c_v` | 0.2 (calibrated 0.225 for the 90° study) |
| Area ratio `A_rect / A_oct` | 1.136 |
| Gauge section | 10 × 50 mm |
| Modelled volume | one layer, reduced coupon |
| `E_sim` | 3901 MPa |
| `ν` | 0.3 |
| `σ_f,sim` | 45.38 MPa |
| Failure criterion | Rankine maximum normal stress |
| Deletion batch | 0.01% of all elements per step |

Published values (Table 2, MPa):

| Angle | `E_exp` | `E_sim` | `σ_f,exp` | `σ_f,sim` (Rankine) | `σ_f,sim` (von Mises) |
|---:|---:|---:|---:|---:|---:|
| 0° | 3434 | 3439 | 39.95 | 39.88 | 39.88 |
| 30° | 2762 | 2962 | 27.47 | 10.65 | 10.65 |
| 45° | 2627 | 2667 | 20.16 | 7.68 | 8.63 |
| 60° | 2334 | 2488 | 14.17 | 7.46 | 8.65 |
| 90° | 2337 | 2385 | 7.24 | 7.39 | 10.72 |

Only the Rankine trend decreases monotonically with angle. The von Mises trend
rises again at 90°, which is why the paper rejects it for brittle PLA.

## Coupon geometry

`src/gcode_fea/benchmark.py` builds the coupon analytically, so the benchmark
does not need a slicer probe. Strands at angle θ to the +x load axis are spaced
one bead width (0.4 mm) apart, clipped to the 10 × 50 mm gauge section, and the
raster direction alternates within a layer.

The analytic coupons use the paper octagon cross-section:

```latex
A_{\text{oct}} = ab\left[1 - \tfrac{1}{2}(1-c_h)(1-c_v)\right]
               = 0.4 \cdot 0.2 \left[1 - 0.5 \cdot 0.3 \cdot 0.8\right]
               = 0.0704\ \text{mm}^2
```

Checked-in reference metrics are in `tests/fixtures/benchmark/coupons.json`:

| Angle | Strands | Volume (mm³) | Void fraction | Intralayer contacts |
|---:|---:|---:|---:|---:|
| 0° | 26 | 91.520 | 0.0848 | 25 |
| 30° | 84 | 88.011 | 0.1199 | 83 |
| 45° | 106 | 88.007 | 0.1199 | 82 |
| 60° | 120 | 87.992 | 0.1201 | 119 |
| 90° | 126 | 88.704 | 0.1130 | 125 |

Void fraction is the octagon corner cut only (~12%); edge/end trim and boundary
strand clipping account for the variation with angle.

## Boundary conditions

The paper models part of the dogbone with the following grip conditions:
x fixed at the centre of the upper and lower ends, y fixed at the respective
corners, z fixed along the upper edge, and the displacement applied to the lower
edge in steps.

The coupon frame uses +x as the load axis and maps those conditions as:

- x fixed at the centre of the left and right edges,
- y fixed at the four corners,
- z fixed along the right edge,
- x displacement on the left edge, applied incrementally.

`Coupon.boundary_conditions` records the recipe; `solvers.coupon.coupon_node_sets`
turns it into `LEFT_EDGE`, `RIGHT_EDGE`, `LEFT_CENTER`, `RIGHT_CENTER`,
`LEFT_CORNERS`, and `RIGHT_CORNERS` node sets.

## Voxel mesh

`src/gcode_fea/voxel.py` samples the swept bead solids into C3D8/HEX8 elements:

- one element per occupied voxel,
- shared nodes between neighbours (conforming),
- element sets `EALL`, `BEAD_<id>`, `LAYER_<n>`, `ROLE_<role>`,
- shared faces between different beads become interface patches
  (intralayer/interlayer) for Phase 3.

Volume convergence for the 0° coupon:

| Voxel size | Elements | Volume (mm³) | Relative error |
|---:|---:|---:|---:|
| 0.20 mm | 13,000 | 104.0 | +13.6% |
| 0.10 mm | 104,000 | 104.0 | +13.6% |
| 0.05 mm | 728,000 | 91.0 | −0.57% |

At 0.2 and 0.1 mm the voxel grid cannot resolve the 0.04 × 0.28 mm corner cut,
so the mesh reproduces the nominal rectangles. Use 0.05 mm or finer when volume
matters. Per-angle 0.05 mm meshes produce 705k–728k elements in ~1.6 s each
with volume errors of 0.57–0.85%.

**Interface-area caveat.** Voxel shared faces overestimate the octagon contact
height when `c_v b < resolution` (0.04 mm at the paper's settings). At 0.05 mm
the 0° coupon reports 125 mm² of intralayer area versus 50 mm² from the analytic
`c_v b L` contact. Cohesive models must scale interface areas from the contact
model, not from the raw voxel face count.

## Solver jobs

Generate all five coupons:

```bash
uv run python -m gcode_fea.benchmark_cli \
  --out out/benchmark --resolution 0.05 --solver calculix
```

Or the equivalent console script:

```bash
uv run gcode-fea-benchmark --out out/benchmark --resolution 0.05 --solver abaqus
```

Each `out/benchmark/coupon_<angle>/` contains:

- `model.inp` — nodes, C3D8 elements, element sets, elastic material, step,
  fixed grips, imposed displacement,
- `deletion_plan.json` — criterion, 0.01% localization fraction, load tolerance,
- `voxel_mesh.json` — resolution, counts, volume error,
- `job.json` — spec, boundary conditions, material, node-set counts.

`out/benchmark/benchmark.json` aggregates geometry metrics, mesh summaries,
interface counts, and (with `--compare`) the paper comparison.

To add results after a solver run, write a JSON file keyed by angle:

```json
{"0": {"E": 3900.0, "sigma_f": 40.1}, "90": {"E": 2380.0, "sigma_f": 7.4}}
```

and pass `--compare results.json`. The report includes relative errors and the
`strength_decreases_with_angle` / `zero_degree_strongest` checks.

## Element deletion and localization

`src/gcode_fea/solvers/element_deletion.py` implements the paper's algorithm:

1. solve the current configuration,
2. evaluate a criterion per active element,
3. sort descending and delete only the top 0.01% of the *total* element count,
4. rebuild the mesh and repeat until the reaction force collapses.

The driver is solver-agnostic. A solver adapter supplies:

```python
solve(active_mask) -> {"stress": (n, 6) float array, "reaction": float}
```

and the driver returns a `DeletionResult` with per-step thresholds, deleted
element IDs, remaining count, reaction, and reaction ratio. It is unit-tested
against synthetic stress fields, including a notch that localizes to one
element and a uniform bar that softens to global failure.

Abaqus/CalculiX element deletion requires either mesh regeneration between
steps (the paper's approach) or solver-specific element-removal features. The
generated `.inp` is the initial model; `deletion_plan.json` is the recipe the
external loop must follow. No solver-specific deletion keyword is emitted
because none is portable between the two targets.

## Validation status

| Acceptance item | Status |
|---|---|
| Octagon mode + area scaling | Pass (`0.0704 mm²`, scale `1.136`) |
| Material card | Pass (`3901 MPa`, `0.3`, `45.38 MPa`) |
| Rankine criterion | Pass (uniaxial, hydrostatic, shear tests) |
| BC/load recipe for 0/30/45/60/90° | Pass (generated and node sets verified) |
| Element deletion + localization | Pass on synthetic stress fields |
| Solver input generation | Pass (Abaqus and CalculiX writers) |
| Strength trend / failure mode | **Pending**: requires a solver run |
| `E`, `σ_f` within 10–15% | **Pending**: requires a solver run |
| Script runs to complete failure | **Pending**: driver tested, not coupled to a solver |
| `docs/paper_benchmark.md` comparison | This document; comparison harness ready |

No solver (Abaqus, CalculiX, Ansys) is installed here, and the disk has limited
free space, so the full 500k-element runs were not executed. The generated jobs
are the artifact to hand to a solver host.

## Limitations

1. Voxel meshes are staircased; volume and interface areas need resolution
   studies (`0.05 mm` or finer for the paper strand geometry).
2. Interface area from voxel faces overestimates the octagon contact until the
   contact height is resolved; use the contact model for cohesive properties.
3. The analytic coupon does not reproduce slicer edge turnarounds; the paper
   found a ~5% effect on `σ_f`, which is inside the benchmark tolerance.
4. The deletion loop is not wired to a live solver. Each step needs a new mesh
   and mapped state.
5. Only one layer is simulated by default, matching the paper's reduced model.
6. The coupon grip conditions are a reduced representation of the full dogbone.

## Reproduction

```bash
uv run python -m unittest discover -s tests -p "test_*.py" -v

uv run python -m gcode_fea.benchmark_cli \
  --out /tmp/bench --angles 0,45,90 --resolution 0.1 --solver calculix

uv run python -m gcode_fea.benchmark_cli \
  --out /tmp/bench-full --resolution 0.05 --solver abaqus
```
