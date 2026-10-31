"""Material cards and paper cross-section scaling.

The paper reports material properties in two conventions:

- ``Eexp`` / ``sigma_exp`` are measured on a rectangular nominal cross-section
  (voids ignored),
- ``Esim`` / ``sigma_sim`` are the values used with the octagon bead model,
  obtained by scaling with ``A_rect / A_oct``.

Keep the two separate. The octagon scaling is an input to the benchmark recipe,
not a material constant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Material:
    """Linear-elastic brittle material card in project units (mm, MPa)."""

    name: str
    youngs_modulus_mpa: float
    poisson_ratio: float
    tensile_strength_mpa: float
    density_g_cm3: float
    failure_criterion: str
    source: str
    notes: str = ""

    def as_card(self) -> dict:
        return asdict(self)


# Seibel & Kiendl (2025), Table 1. E and sigma are already scaled by
# A_rect / A_oct = 1.136 for the octagon strand model (c_h = 0.7, c_v = 0.2).
PAPER_PLA = Material(
    name="paper_pla",
    youngs_modulus_mpa=3901.0,
    poisson_ratio=0.3,
    tensile_strength_mpa=45.38,
    density_g_cm3=1.24,
    failure_criterion="rankine",
    source="Seibel & Kiendl (2025), Table 1",
    notes=(
        "E and sigma_f are scaled by A_rect / A_oct = 1.136 relative to the "
        "experimental curves. Use with the paper_octagon cross-section."
    ),
)


def octagon_area(width: float, height: float, c_h: float, c_v: float) -> float:
    """Area of the paper's octagon strand."""
    _check_fractions(c_h, c_v)
    return width * height * (1.0 - 0.5 * (1.0 - c_h) * (1.0 - c_v))


def paper_area_scale(c_h: float = 0.7, c_v: float = 0.2) -> float:
    """Ratio ``A_rect / A_oct`` used to scale E and sigma_f."""
    _check_fractions(c_h, c_v)
    return 1.0 / (1.0 - 0.5 * (1.0 - c_h) * (1.0 - c_v))


def scaled_material(
    material: Material,
    c_h: float = 0.7,
    c_v: float = 0.2,
) -> tuple[Material, float]:
    """Return a copy of ``material`` scaled to the octagon cross-section."""

    scale = paper_area_scale(c_h, c_v)
    result = Material(
        name=f"{material.name}_octagon",
        youngs_modulus_mpa=material.youngs_modulus_mpa * scale,
        poisson_ratio=material.poisson_ratio,
        tensile_strength_mpa=material.tensile_strength_mpa * scale,
        density_g_cm3=material.density_g_cm3,
        failure_criterion=material.failure_criterion,
        source=material.source,
        notes=(
            f"{material.notes} Scaled by A_rect/A_oct = {scale:.6f} "
            f"for c_h={c_h:g}, c_v={c_v:g}."
        ).strip(),
    )
    return result, scale


def paper_material_card() -> dict:
    return {
        "material": PAPER_PLA.as_card(),
        "cross_section_area_scale": paper_area_scale(0.7, 0.2),
        "failure_criterion": "rankine_max_normal_stress",
        "density_note": "PLA density for reporting only; geometry uses mm and MPa",
    }


def _check_fractions(c_h: float, c_v: float) -> None:
    for name, value in (("c_h", c_h), ("c_v", c_v)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1]")
