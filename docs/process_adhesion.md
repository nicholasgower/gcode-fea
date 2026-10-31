# Phase 4 — Process-informed adhesion

**Date:** 2026-10-31
**Status:** Process extraction, per-bead process records, interface bond quality,
sidecar integration, and the uncertainty envelope are implemented and tested.
The model is uncalibrated, and the experimental validation the roadmap requires
needs physical coupons, which were not run.

## Scope

Phase 3 gives every interface a cohesive card. Phase 4 makes the card spatially
varying: process conditions at each interface set a bond quality, and quality
scales cohesive strength and toughness.

Deliverables implemented:

- G-code process parsing (speed, nozzle temperature, fan, layer time, role,
  width, height),
- per-bead process records with plate-offset-aware matching,
- a lumped thermal/healing model that maps process to bond quality,
- a per-interface `bond_quality` field in the sidecar and report,
- a Monte Carlo uncertainty envelope over the model parameters,
- a cohesive card scaled by bond quality.

Not done: physical validation at different layer times, temperatures, and fan
settings, and the acceptance claim that this beats a global `c_v`.

## Process data

The plugin graph does not expose per-path process data, so Phase 4 parses the
sliced G-code. `src/gcode_fea/process.py` reads:

- `;LAYER_CHANGE`, `;Z:`, `;HEIGHT:`, `;TYPE:`, `;WIDTH:`,
- `M104`/`M109` nozzle temperature,
- `M140`/`M190` bed temperature,
- `M106` fan (0–255 scaled to percent),
- `G1`/`G0` moves, with extrusion detected from a positive `E` in relative mode.

Unsupported constructs (arc moves) are reported in `warnings` instead of being
guessed. Layer times sum travel and extrusion times from the commanded
feedrate; acceleration and firmware limits are not modelled, so times are lower
bounds.

Each bead gets a `BeadProcess` record: speed, nozzle temperature, fan, layer
time, match count, and source. Matching is by layer (`;Z:` vs bead `print_z`)
and by spatial proximity to the bead path.

**Plate offset.** Slicer G-code is in plate coordinates while probe paths are
object-centred. On the 20 mm cube the discovered offset is exactly
`(-100, -100)`, the plate centre. `attach_process` estimates the offset from
the model and G-code XY bounding boxes when it is not supplied.

Result on the 20 mm cube (100 layers, 8541 extrusion segments):

| Metric | Value |
|---|---|
| Beads | 390 |
| Matched to G-code | 390 (100%) |
| Layer time, median | 14.06 s |
| Layer time, range | 13.89–53.66 s |
| Fan, median | 85.9% |
| Nozzle temperature | 200 °C |

## Thermal/healing model

`src/gcode_fea/adhesion.py` uses a two-time lumped cooling model:

```latex
T(t) = T_\infty + (T_0 - T_\infty)
\left[(1-\phi_s)e^{-t/\tau_f} + \phi_s e^{-t/(\tau_f\kappa_s)}\right]
```

with a fan-dependent fast time constant

```latex
\tau_f(f) = \frac{\tau_0}{1 + \alpha f}
```

where `f` is fan percent. The fast term is the strand surface; the slow term
represents heat retained in the deposited layer. A single exponential saturates
well before typical layer times, which would hide the layer-time dependence.

The healing rate is Arrhenius:

```latex
r(T) = \exp\left[-\frac{E_a}{R}
\left(\frac{1}{T} - \frac{1}{T_\mathrm{ref}}\right)\right]
```

and bond quality is

```latex
q = \mathrm{clamp}\left[
\left(\frac{t_d}{t_\mathrm{ref}}\, r(T_c)\right)^n,\ q_\min,\ 1
\right]
```

where `T_c` is the mean of the cooled lower bead surface and the newly deposited
bead's nozzle temperature, and `t_d` is the contact dwell (`bond_window_s`,
capped by the layer time). For interlayer contacts the wait is the lower bead's
layer time; for intralayer contacts it is `raster spacing / speed`, about 0.01 s,
so intralayer bonds score much higher.

The cohesive card scales as

```latex
\sigma(q) = \sigma_0\, q^{a}, \qquad G(q) = G_0\, q^{b}
```

with `a = b = 1` by default.

### Parameters

| Parameter | Default | Status |
|---|---|---|
| `ambient_temp_c` | 25 | printer environment |
| `reference_temp_c` | 200 | reference diffusion temperature |
| `activation_energy_j_mol` | 50,000 | **calibration input** |
| `reference_time_s` | 5.0 | **calibration input** |
| `healing_exponent` | 0.25 | literature-shaped, **calibration input** |
| `bond_window_s` | 5.0 | **calibration input** |
| `cooling_time_s` | 1.0 | **calibration input** |
| `slow_fraction` | 0.3 | **calibration input** |
| `slow_cooling_factor` | 30 | **calibration input** |
| `fan_cooling_factor` | 0.02 per % | **calibration input** |
| `min_quality` | 0.02 | numerical floor |
| `strength_exponent`, `toughness_exponent` | 1.0 | **calibration input** |

The model is flagged `calibrated=False`. No literature values are claimed.

## Results on the 20 mm cube

Interfaces: 789 (386 intralayer, 403 interlayer).

| Quantity | Min | P05 | Median | Mean | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| Bond quality, all | 0.487 | 0.496 | 0.523 | 0.745 | 0.989 | 0.993 |
| Intralayer | 0.970 | 0.982 | 0.983 | 0.985 | 0.989 | 0.993 |
| Interlayer | 0.487 | 0.496 | 0.523 | 0.516 | 0.523 | 0.523 |
| Contact temperature (°C) | 112.6 | 114.4 | 119.9 | 157.2 | 198.3 | 199.0 |

The bimodal quality distribution is expected: intralayer neighbours are printed
within milliseconds, while interlayer contact waits a full layer time. The
interlayer window is narrow because the calibration defaults put the lower bead
close to ambient before the next layer arrives; that narrowness is a model
property to revisit during calibration, not a physical result.

## Uncertainty envelope

`uncertainty_report` samples activation energy (±25%), reference time (×0.7–1.4),
healing exponent (×0.8–1.25), bond window (×0.5–2), cooling time (×0.5–2), slow
fraction (±0.1), slow cooling factor (×0.5–2), fan factor (×0.5–2), and ambient
temperature (±10 °C), with a fixed seed.

200 samples on the cube:

| Quantity | P05 | Median | P95 |
|---|---:|---:|---:|
| Mean bond quality | 0.635 | 0.753 | 0.850 |
| Implied strength factor | 0.635 | 0.753 | 0.850 |

This is a sensitivity envelope, not a confidence interval for a physical
prediction. The calibration domain is stated in every report: PLA on the Ender 3
V2 Neo, 0.1–0.4 mm layers, 190–230 °C nozzle, 0–100% fan, 10–100 s layer times.

## Sidecar and report

`build_sidecar` and `build_report` now take optional `interfaces`,
`bead_process`, `process_summary`, and `adhesion` arguments:

- `interfaces[]`: Phase 3 interface record plus `bond_quality`,
- `process.beads[]`: per-bead speed, temperature, fan, layer time, source,
- `process.summary`: distributions, G-code summary, match coverage and offset,
- `adhesion`: model card, quality statistics by kind, contact temperatures,
  warnings, and per-interface details,
- report summary: `interface_count`, `mean_bond_quality`, `min_bond_quality`,
- report `adhesion` block without the row-level details.

Existing Phase 1 callers are unaffected; the new sections appear only when the
arguments are supplied.

## Commands

```bash
# With G-code process data
uv run gcode-fea-process \
  --probe tests/fixtures/phase1/cube20_probe.json \
  --gcode out/cube20.gcode \
  --out out/process --resolution 0.2 --samples 200

# Without G-code: uniform defaults, coverage reported as 0
uv run gcode-fea-process \
  --probe tests/fixtures/phase0/phase0_probe_obj102.json \
  --out out/process-default --samples 50
```

Outputs: `beads.json` (sidecar with interfaces, process, adhesion),
`report.json`, `process.json`, `adhesion.json`, `uncertainty.json`, and
optionally a `--scaled-card` cohesive JSON.

## Validation status

| Item | Status |
|---|---|
| G-code parsing (layers, temp, fan, roles, widths) | Pass, including a checked-in 2-layer real slice |
| Bead matching and plate offset | Pass (100% on the 20 mm cube) |
| Layer-time estimates | Pass; lower bounds by construction |
| Bond-quality trends | Pass in unit tests: hotter and less fan improve bonding |
| Per-interface sidecar field | Pass |
| Uncertainty envelope | Pass; deterministic for a fixed seed |
| Physical validation vs layer time/fan/temperature | **Pending**: no coupons printed |
| Beats a global `c_v` | **Pending**: needs the coupon campaign |
| Calibration of the model parameters | **Pending** |

## Limitations

1. The model is uncalibrated; every value is a sensitiivity result, not a
   prediction.
2. Layer times ignore acceleration, so they are lower bounds.
3. Thermal history is lumped, not solved; no conduction between beads, bed, or
   chamber.
4. Matching assumes no rotation between probe and G-code frames and the same
   slicer run.
5. The model does not read per-path temperatures beyond M104/M109; slicer
   temperature changes mid-print are tracked as setpoints.
6. Supports, multi-material, and per-path flow are not modelled.
7. Interlayer quality is nearly uniform under the default parameters; the
   calibration campaign must revisit the cooling parameters.

## Reproduction

```bash
uv run python -m unittest discover -s tests -p "test_*.py" -v

uv run python -m gcode_fea.process_cli \
  --probe tests/fixtures/phase1/cube20_probe.json \
  --gcode tests/fixtures/process/cube20_first2layers.gcode \
  --out /tmp/process --resolution 0.2 --samples 25
```
