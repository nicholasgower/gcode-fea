from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.cross_section import solve_cross_section
from gcode_fea.model import Bead, SliceModel
from gcode_fea.process import (
    attach_process,
    default_process,
    parse_gcode,
    process_report,
)

GCODE = """; HEADER
M140 S60
M104 S215
G90
M83
;LAYER_CHANGE
;Z:0.2
;HEIGHT:0.2
M106 S0
;TYPE:Outer wall
;WIDTH:0.45
G1 X100 Y100 F3000
G1 X110 Y100 E0.5 F1800
G1 X110 Y110 E0.5
;TYPE:Sparse infill
;WIDTH:0.42
M106 S255
G1 X105 Y105 E0.5
;LAYER_CHANGE
;Z:0.4
;HEIGHT:0.2
;TYPE:Outer wall
G1 X100 Y100 E0.2
G1 X110 Y100 E0.5 F2400
"""


def make_bead(bead_id: str, layer: int, y: float, z: float) -> Bead:
    cs = solve_cross_section(0.08, 0.2, "volume_conserving", 0.45)
    return Bead(
        bead_id,
        0,
        layer,
        z + 0.2,
        "Outer wall",
        ((0.0, y, z), (10.0, y, z)),
        0.45,
        0.2,
        0.08,
        cs,
    )


class GcodeParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "part.gcode"
        path.write_text(GCODE)
        cls.gcode = parse_gcode(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_layers_and_segments(self) -> None:
        self.assertEqual(self.gcode.segment_count, 5)
        self.assertEqual(len(self.gcode.layers()), 2)
        self.assertIn(0, self.gcode.layer_z)
        self.assertAlmostEqual(self.gcode.layer_z[0], 0.2)
        self.assertAlmostEqual(self.gcode.settings["layer_height"], 0.2)

    def test_temperature_and_fan(self) -> None:
        first = self.gcode.layers()[0]
        self.assertAlmostEqual(first[0].nozzle_temp_c, 215.0)
        self.assertAlmostEqual(first[0].fan_percent, 0.0)
        self.assertAlmostEqual(first[-1].fan_percent, 100.0)

    def test_speeds_and_roles(self) -> None:
        first = self.gcode.layers()[0]
        self.assertAlmostEqual(first[0].speed_mm_s, 30.0)
        self.assertEqual(first[0].role, "Outer wall")
        self.assertEqual(first[-1].role, "Sparse infill")
        self.assertAlmostEqual(first[0].width, 0.45)

    def test_layer_times(self) -> None:
        for layer in self.gcode.layers():
            self.assertGreater(self.gcode.layer_time(layer), 0.0)
        self.assertGreater(self.gcode.total_time_s, 0.0)

    def test_empty_gcode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.gcode"
            path.write_text("; only comments\nG28\n")
            with self.assertRaises(ValueError):
                parse_gcode(path)


class ProcessMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = SliceModel(
            [
                make_bead("a", 0, 0.0, 0.0),
                make_bead("b", 0, 0.4, 0.0),
                make_bead("c", 1, 0.0, 0.2),
            ]
        )
        path = Path(tempfile.mkdtemp()) / "part.gcode"
        path.write_text(GCODE)
        self.gcode = parse_gcode(path)

    def test_default_process(self) -> None:
        records = default_process(self.model)
        self.assertEqual(len(records), 3)
        for record in records.values():
            self.assertEqual(record.source, "default")
            self.assertGreater(record.layer_time_s, 0.0)

    def test_attach_with_plate_offset(self) -> None:
        records, summary = attach_process(self.model, self.gcode, tolerance=1.0)
        self.assertEqual(len(records), 3)
        bbox = self.model.bbox()
        model_center = ((bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0)
        self.assertAlmostEqual(
            summary["origin_offset"][0], model_center[0] - 105.0, places=6
        )
        self.assertAlmostEqual(
            summary["origin_offset"][1], model_center[1] - 105.0, places=6
        )
        self.assertGreater(summary["matched"], 0)
        matched = [record for record in records.values() if record.source == "gcode"]
        self.assertGreater(len(matched), 0)
        for record in matched:
            self.assertGreater(record.matched_segments, 0)
            self.assertGreater(record.speed_mm_s, 0.0)

    def test_process_report(self) -> None:
        records, _ = attach_process(self.model, self.gcode)
        report = process_report(self.model, records)
        self.assertEqual(report["bead_count"], 3)
        self.assertGreater(report["speed_mm_s"]["max"], 0.0)
        self.assertIn("sources", report)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GCODE = REPO_ROOT / "tests" / "fixtures" / "process" / "cube20_first2layers.gcode"
CUBE20_PROBE = REPO_ROOT / "tests" / "fixtures" / "phase1" / "cube20_probe.json"


class RealGcodeFixtureTests(unittest.TestCase):
    def test_parse_real_slice(self) -> None:
        gcode = parse_gcode(REAL_GCODE)
        self.assertEqual(gcode.settings["layer_count"], 2)
        self.assertEqual(gcode.settings["nozzle_temp_c"], 200.0)
        self.assertAlmostEqual(gcode.settings["fan_percent"], 47.8431, places=3)
        self.assertEqual(len(gcode.layers()), 2)
        self.assertGreater(gcode.segment_count, 100)
        for layer, segments in gcode.layers().items():
            self.assertGreater(gcode.layer_time(layer), 0.0)
            self.assertTrue(all(segment.nozzle_temp_c == 200.0 for segment in segments))

    def test_match_real_probe(self) -> None:
        from gcode_fea.cache import load_probe

        model = load_probe(CUBE20_PROBE)
        gcode = parse_gcode(REAL_GCODE)
        records, summary = attach_process(model, gcode)
        self.assertAlmostEqual(summary["origin_offset"][0], -100.0, places=6)
        self.assertAlmostEqual(summary["origin_offset"][1], -100.0, places=6)
        self.assertEqual(summary["matched"], 8)
        matched = [record for record in records.values() if record.source == "gcode"]
        self.assertEqual(len(matched), 8)
        for record in matched:
            self.assertEqual(record.nozzle_temp_c, 200.0)
        self.assertTrue(any(record.fan_percent > 0.0 for record in matched))


if __name__ == "__main__":
    unittest.main()
