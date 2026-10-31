from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cache import load_cache, load_probe, save_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


class ModelCacheTests(unittest.TestCase):
    def test_load_probe(self) -> None:
        model = load_probe(PROBE)
        self.assertEqual(len(model.beads), 3)
        self.assertEqual({bead.layer_index for bead in model.beads}, {0})
        for bead in model.beads:
            self.assertGreater(bead.length, 0)
            self.assertAlmostEqual(
                bead.volume, bead.area_per_length * bead.length, places=10
            )
            self.assertAlmostEqual(bead.bottom_z, 0.0, delta=1e-6)
            self.assertGreater(len(bead.cross_section.profile), 3)

    def test_multilayer_probe(self) -> None:
        base = json.loads(PROBE.read_text())
        layers = []
        for index in range(2):
            layer = dict(base)
            layer["layer_index"] = index
            layer["print_z"] = 0.2 * (index + 1)
            layer["slice_z"] = 0.2 * index + 0.1
            layer["height"] = 0.2
            layers.append(layer)
        payload = {
            "probe_version": 2,
            "orcaslicer_version": "2.5.0-beta",
            "object_id": 102,
            "layer_count": 2,
            "truncated": False,
            "layers": layers,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe2.json"
            path.write_text(json.dumps(payload))
            model = load_probe(path)
            self.assertEqual(len(model.beads), 6)
            self.assertEqual(set(bead.layer_index for bead in model.beads), {0, 1})
            self.assertEqual(len({bead.bead_id for bead in model.beads}), 6)
            self.assertEqual(model.meta["layer_count"], 2)
            for bead in model.beads:
                expected_bottom = 0.0 if bead.layer_index == 0 else 0.2
                self.assertAlmostEqual(bead.bottom_z, expected_bottom, delta=1e-6)

    def test_cache_round_trip(self) -> None:
        model = load_probe(PROBE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            save_cache(model, path)
            loaded = load_cache(path)
            self.assertEqual(
                [bead.bead_id for bead in model.sorted_beads()],
                [bead.bead_id for bead in loaded.sorted_beads()],
            )
            self.assertAlmostEqual(model.volume, loaded.volume, places=12)
            self.assertEqual(
                len(model.beads[0].cross_section.profile),
                len(loaded.beads[0].cross_section.profile),
            )
            self.assertEqual(model.meta["mode"], loaded.meta["mode"])

            second = Path(tmp) / "cache2.json"
            save_cache(model, second)
            self.assertEqual(path.read_text(), second.read_text())


if __name__ == "__main__":
    unittest.main()
