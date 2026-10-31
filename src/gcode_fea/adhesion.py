"""Process-informed interface bond quality.

The model maps process conditions (nozzle temperature, fan, layer time, and the
delay between neighbouring paths) to a bond degree at each interface. Bond
degree then scales cohesive strength and toughness.

This is a calibration scaffold, not a validated thermal model:

- interface temperature is a lumped exponential cooling estimate,
- healing follows an Arrhenius rate with an empirical exponent,
- every parameter is a calibration input with a stated range,
- the model is flagged ``calibrated=False`` until measured data replaces it.

The trends are the ones the literature reports: hotter interfaces, shorter
layer times, and less fan improve bonding; longer waits and stronger cooling
reduce it.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from .cohesive import CohesiveCard
from .interfaces import Interface
from .process import BeadProcess

GAS_CONSTANT = 8.31446261815324  # J/(mol K)


@dataclass(frozen=True)
class AdhesionModel:
    ambient_temp_c: float = 25.0
    reference_temp_c: float = 200.0
    activation_energy_j_mol: float = 5.0e4
    reference_time_s: float = 5.0
    healing_exponent: float = 0.25
    bond_window_s: float = 5.0
    cooling_time_s: float = 1.0
    slow_fraction: float = 0.3
    slow_cooling_factor: float = 30.0
    fan_cooling_factor: float = 0.02
    min_quality: float = 0.02
    max_quality: float = 1.0
    strength_exponent: float = 1.0
    toughness_exponent: float = 1.0
    calibrated: bool = False
    source: str = "initial process-informed model; calibration inputs"
    notes: str = (
        "Lumped cooling plus Arrhenius healing. Not validated. Calibrate "
        "activation energy, cooling time, fan factor, and reference time "
        "against layer-time/fan/temperature experiments."
    )

    def __post_init__(self) -> None:
        if self.cooling_time_s <= 0:
            raise ValueError("cooling_time_s must be positive")
        if self.reference_time_s <= 0:
            raise ValueError("reference_time_s must be positive")
        if self.reference_temp_c <= self.ambient_temp_c:
            raise ValueError("reference_temp_c must exceed ambient_temp_c")
        if not 0.0 <= self.min_quality <= self.max_quality <= 1.0:
            raise ValueError("quality bounds must satisfy 0 <= min <= max <= 1")
        if self.healing_exponent <= 0:
            raise ValueError("healing_exponent must be positive")
        if not 0.0 <= self.slow_fraction < 1.0:
            raise ValueError("slow_fraction must be in [0, 1)")
        if self.slow_cooling_factor <= 1.0:
            raise ValueError("slow_cooling_factor must exceed 1")

    def as_card(self) -> dict:
        return asdict(self)

    def cooling_time(self, fan_percent: float) -> float:
        factor = 1.0 + self.fan_cooling_factor * max(0.0, fan_percent)
        return self.cooling_time_s / factor

    def cooled_temperature(
        self,
        initial_temp_c: float,
        fan_percent: float,
        wait_s: float,
    ) -> float:
        """Two-time cooling: a fast surface term plus a slow bulk term.

        The fast term is the strand surface and responds to the part fan; the
        slow term represents heat retained in the deposited layer and is
        insensitive to fan. A single exponential saturates well before typical
        layer times, which would hide the layer-time dependence.
        """

        wait = max(0.0, wait_s)
        tau_fast = self.cooling_time(fan_percent)
        tau_slow = tau_fast * self.slow_cooling_factor
        fast = (1.0 - self.slow_fraction) * math.exp(-wait / tau_fast)
        slow = self.slow_fraction * math.exp(-wait / tau_slow)
        return self.ambient_temp_c + (initial_temp_c - self.ambient_temp_c) * (
            fast + slow
        )

    def rate(self, temperature_c: float) -> float:
        """Relative diffusion rate at ``temperature_c`` (dimensionless)."""

        temperature_k = max(temperature_c + 273.15, 1.0)
        reference_k = self.reference_temp_c + 273.15
        exponent = -self.activation_energy_j_mol / GAS_CONSTANT * (
            1.0 / temperature_k - 1.0 / reference_k
        )
        return math.exp(exponent)

    def quality(self, temperature_c: float, dwell_s: float) -> float:
        dwell = max(0.0, dwell_s)
        if dwell <= 0:
            return self.min_quality
        rate = self.rate(temperature_c)
        value = (dwell / self.reference_time_s * rate) ** self.healing_exponent
        return min(self.max_quality, max(self.min_quality, value))


@dataclass
class BondDetail:
    interface_id: str
    kind: str
    quality: float
    contact_temp_c: float
    lower_temp_c: float
    dwell_s: float
    wait_s: float
    approach_speed_mm_s: float
    strength_factor: float
    toughness_factor: float


def bond_quality(
    process_a: BeadProcess,
    process_b: BeadProcess,
    kind: str,
    model: AdhesionModel = AdhesionModel(),
    distance_mm: float | None = None,
) -> BondDetail:
    """Bond quality for one interface.

    For interlayer contacts the lower bead has cooled for the upper bead's layer
    time. For intralayer contacts the neighbouring path was printed
    ``distance / speed`` earlier. The interface temperature is the mean of the
    two surface temperatures.
    """

    if kind not in ("intralayer", "interlayer"):
        raise ValueError(f"unknown interface kind: {kind}")
    speed = max(
        1e-6, 0.5 * (process_a.speed_mm_s + process_b.speed_mm_s)
    )
    if kind == "interlayer":
        wait = max(0.0, process_a.layer_time_s)
    else:
        distance = distance_mm if distance_mm is not None else 0.4
        wait = max(0.005, distance / speed)

    lower_temp = model.cooled_temperature(
        process_a.nozzle_temp_c,
        process_a.fan_percent,
        wait,
    )
    contact_temp = 0.5 * (lower_temp + process_b.nozzle_temp_c)
    dwell = min(model.bond_window_s, max(process_b.layer_time_s, 1e-6))
    quality = model.quality(contact_temp, dwell)
    return BondDetail(
        interface_id="",
        kind=kind,
        quality=quality,
        contact_temp_c=contact_temp,
        lower_temp_c=lower_temp,
        dwell_s=dwell,
        wait_s=wait,
        approach_speed_mm_s=speed,
        strength_factor=quality ** model.strength_exponent,
        toughness_factor=quality ** model.toughness_exponent,
    )


def apply_bond_quality(
    interfaces: list[Interface],
    bead_process: dict[str, BeadProcess],
    model: AdhesionModel = AdhesionModel(),
) -> tuple[list[Interface], dict]:
    """Set ``bond_quality`` on each interface and return a summary."""

    details: list[BondDetail] = []
    missing = 0
    for item in interfaces:
        process_a = bead_process.get(item.id_a)
        process_b = bead_process.get(item.id_b)
        if process_a is None or process_b is None:
            missing += 1
            continue
        detail = bond_quality(
            process_a,
            process_b,
            item.kind,
            model,
            distance_mm=_distance(),
        )
        item.bond_quality = detail.quality
        detail.interface_id = item.interface_id
        details.append(detail)

    qualities = [detail.quality for detail in details]
    intralayer = [detail for detail in details if detail.kind == "intralayer"]
    interlayer = [detail for detail in details if detail.kind == "interlayer"]
    summary = {
        "model": model.as_card(),
        "interface_count": len(interfaces),
        "scored": len(details),
        "missing_process": missing,
        "quality": _stats(qualities),
        "intralayer_quality": _stats([detail.quality for detail in intralayer]),
        "interlayer_quality": _stats([detail.quality for detail in interlayer]),
        "contact_temp_c": _stats([detail.contact_temp_c for detail in details]),
        "warnings": (
            ["missing process data for some interfaces"]
            if missing
            else []
        )
        + ([] if model.calibrated else ["adhesion model is not calibrated"]),
        "details": [asdict(detail) for detail in details],
    }
    return interfaces, summary


def quality_scaled_card(
    card: CohesiveCard,
    quality: float,
    model: AdhesionModel = AdhesionModel(),
) -> CohesiveCard:
    """Scale cohesive strength and toughness by bond quality."""

    if not 0.0 < quality <= 1.0:
        raise ValueError("quality must be in (0, 1]")
    return CohesiveCard(
        name=f"{card.name}_q{quality:.4f}",
        sigma_n=card.sigma_n * quality ** model.strength_exponent,
        sigma_s=card.sigma_s * quality ** model.strength_exponent,
        G_Ic=card.G_Ic * quality ** model.toughness_exponent,
        G_IIc=card.G_IIc * quality ** model.toughness_exponent,
        stiffness_n=card.stiffness_n,
        stiffness_s=card.stiffness_s,
        damage_initiation=card.damage_initiation,
        damage_evolution=card.damage_evolution,
        thickness=card.thickness,
        source=f"{card.source}; scaled by process bond quality {quality:.4f}",
        notes=card.notes,
        calibrated=False,
    )


def uncertainty_report(
    interfaces: list[Interface],
    bead_process: dict[str, BeadProcess],
    model: AdhesionModel = AdhesionModel(),
    samples: int = 200,
    seed: int = 0,
) -> dict:
    """Monte Carlo over the adhesion-model parameters.

    Reports the distribution of the mean bond quality and the implied strength
    factor. The ranges are stated so that the result cannot be mistaken for a
    validated prediction.
    """

    if samples < 2:
        raise ValueError("samples must be at least 2")
    generator = random.Random(seed)
    mean_qualities: list[float] = []
    strength_factors: list[float] = []
    parameter_rows: list[dict] = []

    for _ in range(samples):
        sample = AdhesionModel(
            ambient_temp_c=model.ambient_temp_c + generator.uniform(-10.0, 10.0),
            reference_temp_c=model.reference_temp_c,
            activation_energy_j_mol=model.activation_energy_j_mol
            * generator.uniform(0.75, 1.25),
            reference_time_s=model.reference_time_s * generator.uniform(0.7, 1.4),
            healing_exponent=model.healing_exponent
            * generator.uniform(0.8, 1.25),
            bond_window_s=model.bond_window_s * generator.uniform(0.5, 2.0),
            cooling_time_s=model.cooling_time_s * generator.uniform(0.5, 2.0),
            slow_fraction=min(
                0.9, max(0.0, model.slow_fraction + generator.uniform(-0.1, 0.1))
            ),
            slow_cooling_factor=model.slow_cooling_factor
            * generator.uniform(0.5, 2.0),
            fan_cooling_factor=model.fan_cooling_factor
            * generator.uniform(0.5, 2.0),
            min_quality=model.min_quality,
            max_quality=model.max_quality,
            strength_exponent=model.strength_exponent,
            toughness_exponent=model.toughness_exponent,
            calibrated=False,
        )
        qualities = [
            bond_quality(
                bead_process[item.id_a],
                bead_process[item.id_b],
                item.kind,
                sample,
                distance_mm=_distance(),
            ).quality
            for item in interfaces
            if item.id_a in bead_process and item.id_b in bead_process
        ]
        if not qualities:
            continue
        mean_quality = sum(qualities) / len(qualities)
        mean_qualities.append(mean_quality)
        strength_factors.append(mean_quality ** sample.strength_exponent)
        parameter_rows.append(
            {
                "activation_energy_j_mol": sample.activation_energy_j_mol,
                "cooling_time_s": sample.cooling_time_s,
                "slow_fraction": sample.slow_fraction,
                "slow_cooling_factor": sample.slow_cooling_factor,
                "fan_cooling_factor": sample.fan_cooling_factor,
                "bond_window_s": sample.bond_window_s,
                "ambient_temp_c": sample.ambient_temp_c,
                "mean_quality": mean_quality,
            }
        )

    if not mean_qualities:
        raise ValueError("no interfaces could be scored")

    return {
        "schema": "gcode_fea.adhesion_uncertainty.v1",
        "model": model.as_card(),
        "samples": len(mean_qualities),
        "seed": seed,
        "mean_quality": _stats(mean_qualities),
        "strength_factor": _stats(strength_factors),
        "ranges": {
            "activation_energy_j_mol": "x0.75 to x1.25",
            "reference_time_s": "x0.7 to x1.4",
            "healing_exponent": "x0.8 to x1.25",
            "bond_window_s": "x0.5 to x2.0",
            "cooling_time_s": "x0.5 to x2.0",
            "slow_fraction": "+/-0.1",
            "slow_cooling_factor": "x0.5 to x2.0",
            "fan_cooling_factor": "x0.5 to x2.0",
            "ambient_temp_c": "+/-10 C",
        },
        "calibration_domain": (
            "PLA on the Ender 3 V2 Neo: 0.1-0.4 mm layers, 190-230 C nozzle, "
            "0-100% part fan, 10-100 s layer times. Outside this range the "
            "model is extrapolation."
        ),
        "warnings": [
            "parameters are not calibrated; the interval is a sensitivity "
            "envelope, not a confidence interval for a physical prediction"
        ],
        "rows": parameter_rows[:20],
    }


#: Adjacent raster-line spacing used to estimate the intralayer approach time.
RASTER_SPACING_MM = 0.4


def _distance() -> float:
    return RASTER_SPACING_MM


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": 0.0, "median": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count,
        "min": ordered[0],
        "p05": ordered[max(0, int(0.05 * (count - 1)))],
        "median": ordered[count // 2],
        "p95": ordered[min(count - 1, int(math.ceil(0.95 * (count - 1))))],
        "max": ordered[-1],
        "mean": sum(ordered) / count,
    }
