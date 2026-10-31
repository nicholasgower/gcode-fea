"""Cohesive-zone material cards and bilinear traction-separation laws.

The paper's Phase 2 model uses perfect bonding plus element deletion. Phase 3
replaces the geometric proxy with explicit interfaces: each contact patch gets a
cohesive stiffness, strength, and fracture energy, and damage localizes at the
interface instead of deleting bulk elements.

No cohesive parameters are taken from the paper, because it does not publish
them. ``INITIAL_PLA_COHESIVE`` is an explicitly labelled starting estimate that
must be calibrated with peel, shear, or DCB tests. Keep that distinction in any
report or sidecar.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .materials import Material


@dataclass(frozen=True)
class CohesiveCard:
    """Traction-separation interface properties in mm/MPa/N units."""

    name: str
    sigma_n: float
    sigma_s: float
    G_Ic: float
    G_IIc: float
    stiffness_n: float
    stiffness_s: float
    damage_initiation: str = "quadratic_traction"
    damage_evolution: str = "energy_linear"
    thickness: float = 1.0
    source: str = ""
    notes: str = ""
    calibrated: bool = False

    def __post_init__(self) -> None:
        for name in (
            "sigma_n",
            "sigma_s",
            "G_Ic",
            "G_IIc",
            "stiffness_n",
            "stiffness_s",
            "thickness",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    def law(self, mode: str = "I") -> "CohesiveLaw":
        if mode.upper() == "I":
            return CohesiveLaw(
                strength=self.sigma_n,
                fracture_energy=self.G_Ic,
                stiffness=self.stiffness_n,
            )
        if mode.upper() == "II":
            return CohesiveLaw(
                strength=self.sigma_s,
                fracture_energy=self.G_IIc,
                stiffness=self.stiffness_s,
            )
        raise ValueError("mode must be 'I' or 'II'")

    def scaled(self, strength: float = 1.0, toughness: float = 1.0) -> "CohesiveCard":
        return CohesiveCard(
            name=f"{self.name}_s{strength:g}_g{toughness:g}",
            sigma_n=self.sigma_n * strength,
            sigma_s=self.sigma_s * strength,
            G_Ic=self.G_Ic * toughness,
            G_IIc=self.G_IIc * toughness,
            stiffness_n=self.stiffness_n,
            stiffness_s=self.stiffness_s,
            damage_initiation=self.damage_initiation,
            damage_evolution=self.damage_evolution,
            thickness=self.thickness,
            source=self.source,
            notes=self.notes,
            calibrated=self.calibrated,
        )

    def as_card(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CohesiveLaw:
    """Bilinear traction-separation law for one mode."""

    strength: float
    fracture_energy: float
    stiffness: float

    def __post_init__(self) -> None:
        if self.strength <= 0 or self.fracture_energy <= 0 or self.stiffness <= 0:
            raise ValueError("cohesive law parameters must be positive")
        if self.stiffness < self.strength ** 2 / (2.0 * self.fracture_energy):
            raise ValueError(
                "stiffness is too low for the requested strength and energy; "
                "the softening branch would vanish"
            )

    @property
    def critical_separation(self) -> float:
        return self.strength / self.stiffness

    @property
    def failure_separation(self) -> float:
        return 2.0 * self.fracture_energy / self.strength

    def traction(self, separation: float) -> float:
        delta = max(0.0, float(separation))
        delta_0 = self.critical_separation
        delta_f = self.failure_separation
        if delta <= delta_0:
            return self.stiffness * delta
        if delta >= delta_f:
            return 0.0
        return self.strength * (delta_f - delta) / (delta_f - delta_0)

    def damage(self, separation: float) -> float:
        delta = max(0.0, float(separation))
        delta_0 = self.critical_separation
        delta_f = self.failure_separation
        if delta <= delta_0:
            return 0.0
        if delta >= delta_f:
            return 1.0
        return (delta_f / delta) * (delta - delta_0) / (delta_f - delta_0)

    def energy(self, separation: float) -> float:
        """Dissipated energy up to ``separation`` (capped at `G`)."""

        delta = max(0.0, float(separation))
        delta_0 = self.critical_separation
        delta_f = self.failure_separation
        if delta <= delta_0:
            return 0.5 * self.stiffness * delta * delta
        if delta >= delta_f:
            return self.fracture_energy
        traction = self.traction(delta)
        return 0.5 * self.strength * delta_0 + 0.5 * (self.strength + traction) * (
            delta - delta_0
        )

    def to_dict(self) -> dict:
        return {
            "strength": self.strength,
            "fracture_energy": self.fracture_energy,
            "stiffness": self.stiffness,
            "critical_separation": self.critical_separation,
            "failure_separation": self.failure_separation,
        }


# Starting estimate only. The paper's perfect-bond model uses sigma_n equal to
# the bulk strength as an upper bound; the remaining parameters are placeholders
# that must be replaced by measured peel/shear/DCB data.
INITIAL_PLA_COHESIVE = CohesiveCard(
    name="pla_cohesive_initial",
    sigma_n=45.38,
    sigma_s=45.38 / math.sqrt(3.0),
    G_Ic=0.5,
    G_IIc=1.0,
    stiffness_n=1.0e5,
    stiffness_s=1.0e5,
    damage_initiation="quadratic_traction",
    damage_evolution="energy_linear",
    thickness=1.0,
    source="initial estimate; calibrate with peel/shear/DCB",
    notes=(
        "sigma_n equals the paper's perfect-bond bulk strength, so it is an "
        "upper bound. G_Ic, G_IIc, and sigma_s are placeholders, not paper or "
        "literature constants."
    ),
    calibrated=False,
)


def cohesive_from_bulk(
    material: Material,
    strength_fraction: float = 1.0,
    G_Ic: float = 0.5,
    G_IIc: float = 1.0,
    stiffness: float = 1.0e5,
) -> CohesiveCard:
    """Derive a cohesive card from a bulk material, with explicit fractions."""

    if not 0.0 < strength_fraction <= 1.0:
        raise ValueError("strength_fraction must be in (0, 1]")
    return CohesiveCard(
        name=f"{material.name}_cohesive",
        sigma_n=material.tensile_strength_mpa * strength_fraction,
        sigma_s=material.tensile_strength_mpa * strength_fraction / math.sqrt(3.0),
        G_Ic=G_Ic,
        G_IIc=G_IIc,
        stiffness_n=stiffness,
        stiffness_s=stiffness,
        source=f"derived from {material.name} with strength_fraction={strength_fraction:g}",
        notes="toughness and stiffness remain calibration inputs",
        calibrated=False,
    )


def mixed_mode_strength(
    card: CohesiveCard,
    mode_angle_deg: float,
) -> float:
    """Effective traction at quadratic initiation for a mixed-mode direction.

    ``mode_angle_deg`` is 0 for pure mode I and 90 for pure mode II. The
    quadratic stress criterion is solved along that loading direction.
    """

    angle = math.radians(mode_angle_deg)
    normal = math.cos(angle)
    shear = math.sin(angle)
    denominator = math.sqrt(
        (normal / card.sigma_n) ** 2 + (shear / card.sigma_s) ** 2
    )
    return 1.0 / denominator


def mixed_mode_law(
    card: CohesiveCard,
    mode_angle_deg: float,
) -> CohesiveLaw:
    """Equivalent bilinear law along a mixed-mode loading direction."""

    strength = mixed_mode_strength(card, mode_angle_deg)
    angle = math.radians(mode_angle_deg)
    normal = math.cos(angle)
    shear = math.sin(angle)
    energy = 1.0 / (
        (normal ** 2 / card.G_Ic) + (shear ** 2 / card.G_IIc)
    )
    stiffness = 1.0 / (
        (normal ** 2 / card.stiffness_n) + (shear ** 2 / card.stiffness_s)
    )
    return CohesiveLaw(
        strength=strength,
        fracture_energy=energy,
        stiffness=stiffness,
    )


def sensitivity_study(
    card: CohesiveCard = INITIAL_PLA_COHESIVE,
    strength_factors: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0),
    toughness_factors: tuple[float, ...] = (0.5, 1.0, 2.0),
) -> list[dict]:
    """Traction-separation sensitivity matrix for the cohesive card.

    This is the parameter sweep to run against a coupon once a solver is
    available. It reports the law-level response (peak traction, critical and
    failure separations, dissipated energy) so the sweep itself is testable
    without a solver.
    """

    rows: list[dict] = []
    for strength in strength_factors:
        for toughness in toughness_factors:
            scaled = card.scaled(strength=strength, toughness=toughness)
            for mode, angle in (("I", 0.0), ("II", 90.0)):
                law = mixed_mode_law(scaled, angle)
                rows.append(
                    {
                        "strength_factor": strength,
                        "toughness_factor": toughness,
                        "mode": mode,
                        "sigma_n": scaled.sigma_n,
                        "sigma_s": scaled.sigma_s,
                        "G_Ic": scaled.G_Ic,
                        "G_IIc": scaled.G_IIc,
                        "peak_traction": law.strength,
                        "critical_separation": law.critical_separation,
                        "failure_separation": law.failure_separation,
                        "fracture_energy": law.fracture_energy,
                    }
                )
    return rows


def calibration_plan() -> dict:
    """Suggested test matrix and extraction workflow for the cohesive card."""

    return {
        "tests": [
            {
                "name": "peel",
                "mode": "I",
                "measures": ["peak traction", "mode I fracture energy"],
                "derives": ["sigma_n", "G_Ic"],
            },
            {
                "name": "lap_shear",
                "mode": "II",
                "measures": ["peak shear traction", "mode II fracture energy"],
                "derives": ["sigma_s", "G_IIc"],
            },
            {
                "name": "dcb",
                "mode": "I",
                "measures": ["R-curve", "mode I fracture energy"],
                "derives": ["G_Ic"],
            },
            {
                "name": "mixed_mode_bend",
                "mode": "mixed",
                "measures": ["initiation envelope"],
                "derives": ["damage_initiation"],
            },
        ],
        "steps": [
            "Print coupons with the reference printer profile and record settings.",
            "Extract peak traction and dissipated energy from each test.",
            "Set sigma_n, sigma_s, G_Ic, G_IIc; keep stiffness high enough that "
            "critical separation is well below the interface element size.",
            "Validate the card on the 0/30/45/60/90 coupon set.",
        ],
        "reference_printer": "Creality Ender 3 V2 Neo",
        "note": (
            "No physical tests were performed in this phase; the plan is the "
            "contract for when they are."
        ),
    }


@dataclass
class CohesiveSummary:
    card: CohesiveCard
    rows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"card": self.card.as_card(), "rows": self.rows}
