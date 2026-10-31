# Phase 3 — Cohesive interfaces

**Date:** 2026-10-31
**Status:** Interface extraction, cohesive cards, mesh splitting, and the Abaqus
cohesive template are implemented and tested. The cohesive job has not been run
in a solver, and the card is an explicitly labelled starting estimate rather
than calibrated data. No physical tests were performed.

## Scope

Phase 2 used the paper's geometric proxy: perfect bonding plus element deletion.
Phase 3 replaces the proxy with explicit interfaces that carry stiffness,
strength, fracture energy, and damage evolution, so failure localizes at bead
contacts instead of deleting bulk elements.

Implemented:

- cohesive material cards and bilinear traction-separation laws,
- mixed-mode quadratic initiation and BK energy evolution,
- interface extraction with contact-area correction,
- mesh splitting at interfaces and zero-thickness COH3D8 elements,
- an Abaqus cohesive input writer with scaled per-interface materials,
- a traction-separation sensitivity matrix,
- a calibration plan for peel/shear/DCB data.

## Cohesive card

`src/gcode_fea/cohesive.py` defines `CohesiveCard`:

| Field | Meaning |
|---|---|
| `sigma_n` | mode I interface strength |
| `sigma_s` | mode II interface shear strength |
| `G_Ic` | mode I fracture energy |
| `G_IIc` | mode II fracture energy |
| `stiffness_n`, `stiffness_s` | penalty stiffness |
| `damage_initiation` | quadratic traction criterion |
| `damage_evolution` | linear energy softening, BK mixed mode |
| `thickness` | constitutive thickness used by the solver |
| `calibrated` | false until measured data replaces the estimate |

`INITIAL_PLA_COHESIVE` sets `sigma_n` to the paper's perfect-bond bulk strength
(45.38 MPa) as an upper bound. `sigma_s`, `G_Ic`, `G_IIc`, and the stiffness are
placeholders. The paper publishes no cohesive parameters, so none are claimed.

`CohesiveLaw` is a bilinear law with

```latex
\delta_0 = \frac{\sigma_{\max}}{K}, \qquad
\delta_f = \frac{2G}{\sigma_{\max}}, \qquad
t(\delta) = \sigma_{\max}\frac{\delta_f-\delta}{\delta_f-\delta_0}
```

for the softening branch. Its peak traction, dissipated energy, and damage are
unit-tested against the closed-form values.

Mixed-mode strength follows the quadratic stress criterion:

```latex
\left(\frac{\sigma_n}{\sigma_{n,\max}}\right)^2 +
\left(\frac{\sigma_s}{\sigma_{s,\max}}\right)^2 = 1
```

and mixed-mode energy follows the inverse rule. The sensitivity sweep reports
peak traction, critical/failure separation, and fracture energy for strength
factors 0.25–1.0 and toughness factors 0.5–2.0, in both modes.

## Interface extraction

`src/gcode_fea/interfaces.py` turns contacts into `Interface` records:

- `id_a`, `id_b`, `kind`, `normal`, `centroid` from the contact model,
- `area` from the contact model, not from raw mesh faces,
- `faces` from the voxel mesh when one is supplied,
- `area_scale = area_contact / area_faces`,
- `area_model` records how the area was derived.

Area rules:

- **interlayer**: the 2.5D contact area already includes the flat-top fraction
  `c_h`.
- **intralayer**: the 2.5D contact area is boundary length × bead height; it is
  scaled by the flat-side fraction `c_v` when the cross-section has one
  (`flat_side_scaled`). Barrel cross-sections have no flat side, so they keep
  the height proxy and are flagged `contact_proxy`.

This correction matters: on the paper coupon at 0.05 mm the raw voxel faces
report 125 mm² of intralayer area versus 50 mm² from `c_v b L`.

## Mesh splitting

`src/gcode_fea/solvers/cohesive_input.py`:

1. groups solid elements into connected components using shared faces,
2. cuts the graph between different beads,
3. duplicates every interface node once per component,
4. rebuilds solid element connectivity with the copies,
5. inserts one zero-thickness COH3D8 element per interface quad,
6. scales the cohesive material by `area_scale` for each interface.

Scaling stiffness, strength, and energy by `A_contact / A_face` makes a
full-size cohesive element reproduce the force and energy of the true contact
patch, and it leaves the characteristic separations unchanged.

Material groups are keyed by the rounded area scale, so a coupon gets a handful
of cohesive materials (`CZM_02200`, `CZM_08050`, ...) instead of one per
interface. Every element is also in `EALL`, `COHESIVE`, `COH_<KIND>`,
`BEAD_<id>`, `LAYER_<n>`, and `ROLE_<role>` sets.

Example for a 4 × 2 mm, two-layer 90° coupon at 0.1 mm:

- 3520 solid elements, 1680 cohesive elements, 9345 nodes,
- 31 interface pairs (20 intralayer, 11 interlayer),
- 8.84 mm² total contact-model area vs 16.80 mm² raw face area.

## Abaqus template

```bash
uv run gcode-fea-cohesive \
  --out out/cohesive --angles 0,45,90 \
  --length 10 --width 4 --layers 2 --resolution 0.1
```

Each coupon directory contains `model.inp`, `interfaces.json`,
`cohesive_card.json`, and `job.json`; `cohesive_benchmark.json` aggregates the
angles, the calibration plan, and the sensitivity matrix.

The deck contains:

- `*ELEMENT, TYPE=C3D8` solids and `*ELEMENT, TYPE=COH3D8` interfaces,
- `*COHESIVE SECTION, RESPONSE=TRACTION SEPARATION, THICKNESS=SPECIFIED`,
- `*ELASTIC, TYPE=TRACTION`,
- `*DAMAGE INITIATION, CRITERION=QUADS`,
- `*DAMAGE EVOLUTION, TYPE=ENERGY, SOFTENING=LINEAR, MIXED MODE BEHAVIOR=BK`,
- the coupon grip boundary conditions and a monotonic displacement step,
- `SDEG` and `STATUS` output for damage tracking.

CalculiX cohesive input is not generated. CalculiX support for this element
form is solver-version dependent, and only Abaqus is claimed as an end-to-end
target; a CalculiX template is future work.

## Calibration workflow

`calibration_plan()` records the contract:

1. print coupons with the Ender 3 V2 Neo profile and record all settings,
2. measure peak traction and dissipated energy in peel, lap-shear, and DCB
   tests,
3. set `sigma_n`, `sigma_s`, `G_Ic`, `G_IIc`; keep the stiffness high enough
   that `delta_0` is well below the interface element size,
4. validate on the 0/30/45/60/90 coupon set.

No physical tests were run, so the card remains uncalibrated and every output
carries that flag.

## Validation status

| Item | Status |
|---|---|
| Bilinear law peak, energy, damage | Pass (closed-form unit tests) |
| Mixed-mode initiation and energy | Pass |
| Interface extraction with area correction | Pass on 2-layer coupons |
| Mesh splitting and COH3D8 insertion | Pass (node pairing, counts, sets) |
| Abaqus cohesive deck structure | Pass (keyword and count checks) |
| Area-scaled materials | Pass |
| Solver run and crack development | **Pending**: no solver installed |
| Interface mesh convergence | **Pending** |
| Clean-checkout reproduction | Pass for generation; solver step pending |
| Trend vs paper/experiments | **Pending** |
| Sensitivity study | Matrix generated and unit-tested; solver sweep pending |

## Comparison with the Phase 2 proxy

The geometric proxy is cheap and robust: it needs only the bulk mesh and a
criterion, and it reproduces the paper's monotonic strength trend with the
Rankine criterion. Its weakness is that "failure" is element deletion in bulk,
so stiffness is lost in a volume rather than on a surface, and interface
strength/toughness cannot be varied independently of the bead geometry.

The cohesive model:

- localizes damage to a zero-thickness surface,
- separates interface strength and toughness from bulk elasticity,
- allows per-interface properties (the Phase 4 hook),
- costs one cohesive element per shared face plus duplicated nodes.

Until a solver run exists, the comparison is structural only; no strength claim
is made.

## Limitations

1. Abaqus only; no CalculiX cohesive template yet.
2. `INITIAL_PLA_COHESIVE` is uncalibrated and must not be reported as measured.
3. Multi-bead junction nodes get one copy per component and one cohesive element
   per pair; very small junction elements are possible.
4. Cohesive area inherits the 2.5D contact resolution.
5. Zero-thickness elements use constitutive thickness 1.0; separations are
   strain-like and the card stiffness must match that convention.
6. No initial damage, contact friction, or cyclic loading.
7. Cohesive element counts are high (one per interface quad); benchmark-scale
   jobs are large.

## Reproduction

```bash
uv run python -m unittest discover -s tests -p "test_*.py" -v

uv run python -m gcode_fea.cohesive_cli \
  --out /tmp/cohesive --angles 0,90 --length 4 --width 2 --resolution 0.1
```
