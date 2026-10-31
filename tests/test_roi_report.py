from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.contacts import detect_contacts
from gcode_fea.cross_section import solve_cross_section
from gcode_fea.model import Bead, SliceModel
from gcode_fea.report import build_report, build_sidecar, write_report, write_sidecar
from gcode_fea.sweep import mesh_stats


def make_bead(bead_id: str, layer: int, x: float, z: float) -> Bead:
    cs = solve_cross_section(0.08, 0.2, "volume_conserving", 0.45)
    return Bead(
        bead_id,
        0,
        layer,
        z + 0.2,
        "test",
        ((x, 0.0, z), (x + 5.0, 0.0, z)),
        0.45,
        0.2,
        0.08,
        cs,
    )


class RoiReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = SliceModel(
            [
                make_bead("a", 0, 0.0, 0.0),
                make_bead("b", 0, 20.0, 0.0),
                make_bead("c", 1, 0.0, 0.2),
            ]
        )

    def test_bbox_roi(self) -> None:
        roi = self.model.filter_roi(bbox=(-1.0, -1.0, -1.0, 10.0, 1.0, 0.1))
        self.assertEqual([bead.bead_id for bead in roi.sorted_beads()], ["a"])

    def test_layer_roi(self) -> None:
        roi = self.model.filter_roi(layer_range=(1, 1))
        self.assertEqual([bead.bead_id for bead in roi.sorted_beads()], ["c"])

    def test_report_hash_is_deterministic(self) -> None:
        contacts = detect_contacts(self.model, resolution=0.2)
        mesh = mesh_stats(self.model)
        first = build_report(self.model, contacts, 0.2, mesh)
        second = build_report(self.model, contacts, 0.2, mesh)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["summary"]["bead_count"], 3)
        self.assertEqual(first["summary"]["layer_count"], 2)

    def test_sidecar_round_trip(self) -> None:
        contacts = detect_contacts(self.model, resolution=0.2)
        sidecar = build_sidecar(self.model, contacts)
        self.assertEqual(sidecar["schema"], "gcode_fea.sidecar.v1")
        self.assertEqual(len(sidecar["beads"]), 3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.json"
            write_sidecar(self.model, contacts, path)
            loaded = json.loads(path.read_text())
            self.assertEqual(len(loaded["beads"]), 3)
            report_path = Path(tmp) / "report.json"
            write_report(self.model, contacts, report_path, resolution=0.2)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["summary"]["bead_count"], 3)


if __name__ == "__main__":
    unittest.main()
