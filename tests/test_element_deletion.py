from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.solvers.element_deletion import (
    PAPER_DELETION_FRACTION,
    criterion_values,
    localize,
    principal_stresses,
    rankine,
    run_deletion_loop,
    von_mises,
    write_deletion_plan,
)


class CriterionTests(unittest.TestCase):
    def test_rankine_uniaxial(self) -> None:
        self.assertAlmostEqual(rankine([100.0, 0, 0, 0, 0, 0]), 100.0)
        self.assertAlmostEqual(rankine([0, 0, 0, 0, 0, 0]), 0.0)
        # Compression does not fail in the tension-dominated Rankine criterion.
        self.assertEqual(rankine([-50.0, 0, 0, 0, 0, 0]), 0.0)

    def test_rankine_is_max_principal(self) -> None:
        stress = [10.0, 20.0, 5.0, 0.0, 0.0, 0.0]
        principal = principal_stresses(stress)
        self.assertAlmostEqual(principal[-1], 20.0)
        self.assertAlmostEqual(rankine(stress), 20.0)

    def test_von_mises_matches_hand_calculation(self) -> None:
        self.assertAlmostEqual(von_mises([100.0, 0, 0, 0, 0, 0]), 100.0)
        hydrostatic = [50.0, 50.0, 50.0, 0.0, 0.0, 0.0]
        self.assertAlmostEqual(von_mises(hydrostatic), 0.0, places=12)
        shear = [0.0, 0.0, 0.0, 40.0, 0.0, 0.0]
        self.assertAlmostEqual(von_mises(shear), 40.0 * np.sqrt(3.0), places=9)

    def test_vectorised(self) -> None:
        values = criterion_values(
            np.array([[10.0, 0, 0, 0, 0, 0], [0.0, 0, 0, 0, 0, 0]]),
            "rankine",
        )
        np.testing.assert_allclose(values, [10.0, 0.0])

    def test_unknown_criterion(self) -> None:
        with self.assertRaises(ValueError):
            criterion_values([1.0, 0, 0, 0, 0, 0], "nope")


class LocalizationTests(unittest.TestCase):
    def test_fraction_is_of_total(self) -> None:
        values = np.arange(10_000, dtype=float)
        deleted = localize(values, fraction=1e-4)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(int(deleted[0]), 9999)

    def test_respects_active_mask(self) -> None:
        values = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        active = np.array([False, False, True, True, True])
        deleted = localize(values, active=active, fraction=0.4, min_count=1)
        self.assertEqual([int(index) for index in deleted], [2, 3])

    def test_min_count(self) -> None:
        deleted = localize(np.ones(10), fraction=1e-6)
        self.assertEqual(len(deleted), 1)

    def test_bad_fraction(self) -> None:
        with self.assertRaises(ValueError):
            localize(np.ones(4), fraction=0.0)


class DeletionLoopTests(unittest.TestCase):
    def test_uniform_bar_softens_and_stops(self) -> None:
        n = 200
        calls = {"count": 0}

        def solve(active):
            calls["count"] += 1
            # Uniform stress: every active element is equally critical.
            stress = np.tile(np.array([10.0, 0, 0, 0, 0, 0]), (n, 1))
            stress[~active] = 0.0
            reaction = 10.0 * active.sum() / n
            return {"stress": stress, "reaction": reaction}

        result = run_deletion_loop(
            solve,
            n,
            fraction=0.05,
            max_steps=100,
            max_reaction_drop=0.5,
        )
        self.assertTrue(result.converged)
        self.assertGreater(result.deleted_elements, 0)
        self.assertLess(result.final_reaction, 0.5 * result.initial_reaction)
        self.assertEqual(result.steps[0].criterion, "rankine")
        self.assertEqual(
            result.steps[0].deleted,
            tuple(range(10)),
        )

    def test_stress_concentrated_at_crack(self) -> None:
        n = 50
        removed: list[int] = []

        def solve(active):
            stress = np.zeros((n, 6))
            stress[active, 0] = 1.0
            stress[0, 0] = 100.0  # notch element
            return {"stress": stress, "reaction": float(active.sum())}

        result = run_deletion_loop(
            solve, n, fraction=1.0 / n, max_steps=1, max_reaction_drop=0.99
        )
        self.assertEqual(result.steps[0].deleted, (0,))
        self.assertEqual(result.deleted_elements, 1)

    def test_result_round_trip(self) -> None:
        def solve(active):
            stress = np.tile(np.array([1.0, 0, 0, 0, 0, 0]), (10, 1))
            return {"stress": stress, "reaction": float(active.sum())}

        result = run_deletion_loop(
            solve, 10, fraction=0.1, max_steps=2, max_reaction_drop=0.99
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = result.write(Path(tmp) / "history.json")
            payload = json.loads(path.read_text())
            self.assertEqual(payload["criterion"], "rankine")
            self.assertEqual(payload["total_elements"], 10)
            self.assertIsInstance(payload["steps"][0]["deleted"], list)

    def test_deletion_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_deletion_plan(Path(tmp) / "plan.json")
            payload = json.loads(path.read_text())
            self.assertEqual(
                payload["fraction_of_total_elements"], PAPER_DELETION_FRACTION
            )
            self.assertIn("localization", payload)

    def test_empty_model(self) -> None:
        with self.assertRaises(ValueError):
            run_deletion_loop(lambda active: {}, 0)


if __name__ == "__main__":
    unittest.main()
