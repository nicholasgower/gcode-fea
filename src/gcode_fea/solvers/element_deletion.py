"""Element deletion, localization, and failure criteria.

The paper's workflow (Seibel & Kiendl 2025, Sect. 2.2.3) deletes only the
highest-scoring elements per load step instead of deleting every element above
the criterion. That localization step is what produces a discrete crack rather
than simultaneous failure of all intralayer zones.

This module is solver-agnostic. A solver adapter supplies, for the currently
active element set, the element stresses and a reaction force; the driver picks
the next deletion batch and repeats until the specimen has lost load capacity.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

Array = np.ndarray

#: Symmetric stress component order used throughout: xx, yy, zz, xy, yz, zx.
COMPONENTS = ("xx", "yy", "zz", "xy", "yz", "zx")

#: The paper deletes 0.01% of the total element count per load step.
PAPER_DELETION_FRACTION = 1e-4


@dataclass(frozen=True)
class DeletionStep:
    step: int
    criterion: str
    threshold: float
    deleted: tuple[int, ...]
    remaining: int
    reaction: float
    reaction_ratio: float


def principal_stresses(stress: Array) -> Array:
    """Return principal stresses (ascending) for a (..., 6) stress array."""

    values = np.asarray(stress, dtype=float)
    single = values.ndim == 1
    if single:
        values = values[None, :]
    if values.shape[-1] != 6:
        raise ValueError("stress must have 6 components: xx, yy, zz, xy, yz, zx")
    xx, yy, zz, xy, yz, zx = (values[..., i] for i in range(6))
    tensor = np.zeros(values.shape[:-1] + (3, 3), dtype=float)
    tensor[..., 0, 0] = xx
    tensor[..., 1, 1] = yy
    tensor[..., 2, 2] = zz
    tensor[..., 0, 1] = tensor[..., 1, 0] = xy
    tensor[..., 1, 2] = tensor[..., 2, 1] = yz
    tensor[..., 0, 2] = tensor[..., 2, 0] = zx
    result = np.linalg.eigvalsh(tensor)
    return result[0] if single else result


def rankine(stress: Array) -> Array:
    """Rankine (maximum normal stress) equivalent stress.

    The paper defines the tension-dominated branch, Eq. 6: ``sigma_eq = sigma_I``
    when ``sigma_I >= 0`` and ``sigma_I >= |sigma_III|``. This implementation
    uses ``max(sigma_I, 0)``, which matches Eq. 6 on that branch and treats
    compression as non-failing. The difference only matters for stress states
    outside the paper's calibration domain.
    """

    values = np.asarray(stress, dtype=float)
    single = values.ndim == 1
    principal = principal_stresses(values)
    if single:
        return max(float(principal[-1]), 0.0)
    return np.maximum(principal[..., -1], 0.0)


def von_mises(stress: Array) -> Array:
    """Von Mises equivalent stress from principal stresses (Eq. 5)."""

    values = np.asarray(stress, dtype=float)
    single = values.ndim == 1
    principal = principal_stresses(values)
    if single:
        s1, s2, s3 = (float(x) for x in principal)
        return math.sqrt(
            ((s1 - s2) ** 2 + (s2 - s3) ** 2 + (s3 - s1) ** 2) / 2.0
        )
    s1, s2, s3 = principal[..., 0], principal[..., 1], principal[..., 2]
    return np.sqrt(
        ((s1 - s2) ** 2 + (s2 - s3) ** 2 + (s3 - s1) ** 2) / 2.0
    )


CRITERIA = {"rankine": rankine, "von_mises": von_mises}


def criterion_values(stress: Array, criterion: str = "rankine") -> Array:
    try:
        function = CRITERIA[criterion]
    except KeyError as exc:
        raise ValueError(f"unknown criterion: {criterion}") from exc
    return function(stress)


def localize(
    values: Array,
    active: Array | None = None,
    fraction: float = PAPER_DELETION_FRACTION,
    min_count: int = 1,
) -> Array:
    """Indices of the highest-scoring active elements for one deletion batch.

    ``fraction`` is a fraction of the *total* element count (matching the
    paper's 0.01% of all elements), not of the active count.
    """

    values = np.asarray(values, dtype=float)
    if values.ndim != 1:
        raise ValueError("values must be one-dimensional")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    if active is None:
        mask = np.ones(values.shape[0], dtype=bool)
    else:
        mask = np.asarray(active, dtype=bool)
        if mask.shape != values.shape:
            raise ValueError("active mask must match values")
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return np.empty(0, dtype=np.int64)
    count = max(int(min_count), int(math.ceil(fraction * values.shape[0])))
    count = min(count, indices.size)
    order = indices[np.argsort(-values[indices], kind="stable")]
    return order[:count].astype(np.int64)


@dataclass
class DeletionResult:
    criterion: str
    fraction: float
    steps: list[DeletionStep]
    total_elements: int
    deleted_elements: int
    final_reaction: float
    initial_reaction: float
    converged: bool

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["steps"] = [
            {
                **asdict(step),
                "deleted": list(step.deleted),
            }
            for step in self.steps
        ]
        return payload

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        )
        return target


def run_deletion_loop(
    solve,
    n_elements: int,
    criterion: str = "rankine",
    fraction: float = PAPER_DELETION_FRACTION,
    max_steps: int = 10_000,
    load_tolerance: float = 1e-6,
    max_reaction_drop: float = 0.5,
) -> DeletionResult:
    """Drive element deletion until the model loses load capacity.

    ``solve(active_mask)`` must return ``{"stress": (n, 6) array,
    "reaction": float}`` for the active elements. The driver:

    1. solves the current configuration,
    2. deletes the top ``fraction`` of active elements by criterion value,
    3. repeats until no elements remain, or the reaction falls below
       ``load_tolerance`` times its initial value, or the reaction has dropped
       below ``1 - max_reaction_drop`` of the initial value.
    """

    if n_elements <= 0:
        raise ValueError("n_elements must be positive")
    active = np.ones(n_elements, dtype=bool)
    steps: list[DeletionStep] = []
    initial_reaction = 0.0
    reaction = 0.0

    for step in range(1, max_steps + 1):
        if not active.any():
            break
        result = solve(active)
        stress = np.asarray(result["stress"], dtype=float)
        reaction = float(result["reaction"])
        if step == 1:
            initial_reaction = reaction
        values = criterion_values(stress, criterion)
        deleted = localize(values, active, fraction)
        if deleted.size == 0:
            break
        threshold = float(np.min(values[deleted]))
        active[deleted] = False
        steps.append(
            DeletionStep(
                step=step,
                criterion=criterion,
                threshold=threshold,
                deleted=tuple(int(index) for index in deleted),
                remaining=int(active.sum()),
                reaction=reaction,
                reaction_ratio=(
                    reaction / initial_reaction if initial_reaction else 0.0
                ),
            )
        )
        if initial_reaction > 0 and reaction <= load_tolerance * initial_reaction:
            break
        if (
            step > 1
            and initial_reaction > 0
            and reaction < (1.0 - max_reaction_drop) * initial_reaction
        ):
            break

    final_reaction = steps[-1].reaction if steps else reaction
    return DeletionResult(
        criterion=criterion,
        fraction=fraction,
        steps=steps,
        total_elements=n_elements,
        deleted_elements=int(n_elements - active.sum()),
        final_reaction=final_reaction,
        initial_reaction=initial_reaction,
        converged=bool(steps) and final_reaction < initial_reaction,
    )


def write_deletion_plan(
    path: str | Path,
    criterion: str = "rankine",
    fraction: float = PAPER_DELETION_FRACTION,
    max_steps: int = 10_000,
    load_tolerance: float = 1e-6,
) -> Path:
    """Write the solver-side deletion recipe as JSON."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "gcode_fea.deletion_plan.v1",
        "criterion": criterion,
        "fraction_of_total_elements": fraction,
        "max_steps": max_steps,
        "load_tolerance": load_tolerance,
        "localization": (
            "Sort active elements by criterion value, delete only the highest "
            "fraction; do not delete every element above the threshold."
        ),
        "reference": "Seibel & Kiendl (2025), Sect. 2.2.3",
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target
