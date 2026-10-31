from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cache import load_probe, save_cache, source_fingerprint
from gcode_fea.config import (
    PluginConfig,
    cache_status,
    parse_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = PluginConfig()
        self.assertEqual(config.mode, "volume_conserving")
        self.assertEqual(config.formats, ("sidecar", "report"))
        self.assertFalse(config.write_step)
        payload = config.to_dict()
        self.assertEqual(payload["resolution"], 0.1)

    def test_round_trip(self) -> None:
        config = PluginConfig(
            mode="paper_octagon",
            c_h=0.7,
            c_v=0.2,
            roi=(0.0, 0.0, 0.0, 10.0, 10.0, 5.0),
            layer_range=(0, 3),
            write_step=True,
        )
        loaded = PluginConfig.from_dict(config.to_dict())
        self.assertEqual(loaded, config)
        self.assertEqual(
            parse_config(json.dumps(config.to_dict())), config
        )

    def test_invalid_values(self) -> None:
        cases = [
            {"mode": "nope"},
            {"resolution": 0.0},
            {"c_h": 1.5},
            {"c_v": -0.1},
            {"roi": (0.0, 0.0)},
            {"layer_range": (3, 1)},
            {"formats": ["sidecar", "pdf"]},
            {"step_stride": 0},
            {"step_tolerance": -0.1},
            {"max_layers": 0},
        ]
        for payload in cases:
            with self.assertRaises(ValueError, msg=payload):
                PluginConfig.from_dict(payload)

    def test_parse_bad_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_config("{not json")
        with self.assertRaises(ValueError):
            parse_config("[]")

    def test_path_resolution(self) -> None:
        storage = Path("/tmp/plugin")
        config = PluginConfig()
        self.assertEqual(config.resolve_cache_path(storage), storage / "cache" / "slice.json")
        self.assertEqual(config.resolve_output_dir(storage), storage / "exports")
        self.assertIsNone(config.resolve_probe_path(storage))
        custom = PluginConfig(cache_path="/a/b.json", output_dir="/c")
        self.assertEqual(custom.resolve_cache_path(storage), Path("/a/b.json"))
        self.assertEqual(custom.resolve_output_dir(storage), Path("/c"))


class CacheStatusTests(unittest.TestCase):
    def test_missing(self) -> None:
        status = cache_status("/tmp/definitely-missing-cache.json")
        self.assertFalse(status["exists"])
        self.assertIn("slice", status["message"])

    def test_fresh_and_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            probe = tmp_path / "probe.json"
            shutil.copy(PROBE, probe)
            model = load_probe(probe)
            model.meta["source_hash"] = source_fingerprint(probe)
            cache = tmp_path / "cache.json"
            save_cache(model, cache)

            fresh = cache_status(cache, probe)
            self.assertTrue(fresh["exists"])
            self.assertFalse(fresh["stale"])
            self.assertEqual(fresh["bead_count"], 3)
            self.assertEqual(fresh["layer_count"], 1)
            self.assertTrue(fresh["slice_hash"])

            probe.write_text(probe.read_text() + "\n")
            stale = cache_status(cache, probe)
            self.assertTrue(stale["stale"])

            unknown = cache_status(cache)
            self.assertIsNone(unknown["stale"])
            self.assertIn("not checked", unknown["message"])


if __name__ == "__main__":
    unittest.main()
