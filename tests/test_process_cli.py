from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"

GCODE = """; synthetic process fixture
M104 S210
G90
M83
;LAYER_CHANGE
;Z:0.2
;HEIGHT:0.2
M106 S128
;TYPE:Outer wall
;WIDTH:0.45
G1 X100 Y100 F3000
G1 X110 Y100 E0.5 F1800
G1 X110 Y110 E0.5
;TYPE:Sparse infill
G1 X105 Y105 E0.5
"""


class ProcessCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get(
            "PYTHONPATH", ""
        )
        return subprocess.run(
            [sys.executable, "-m", "gcode_fea.process_cli", *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_process_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gcode = Path(tmp) / "part.gcode"
            gcode.write_text(GCODE)
            out = Path(tmp) / "out"
            completed = self.run_cli(
                "--probe",
                str(PROBE),
                "--gcode",
                str(gcode),
                "--out",
                str(out),
                "--resolution",
                "0.2",
                "--samples",
                "5",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            for name in (
                "beads.json",
                "report.json",
                "process.json",
                "adhesion.json",
                "uncertainty.json",
            ):
                self.assertTrue((out / name).exists(), name)

            sidecar = json.loads((out / "beads.json").read_text())
            self.assertIn("interfaces", sidecar)
            self.assertIn("process", sidecar)
            self.assertIn("adhesion", sidecar)
            self.assertTrue(
                all("bond_quality" in item for item in sidecar["interfaces"])
            )

            report = json.loads((out / "report.json").read_text())
            self.assertIsNotNone(report["summary"]["interface_count"])
            self.assertIsNotNone(report["summary"]["mean_bond_quality"])
            self.assertTrue(
                any("not calibrated" in warning for warning in report["warnings"])
            )

            process = json.loads((out / "process.json").read_text())
            self.assertEqual(process["summary"]["bead_count"], 3)

            uncertainty = json.loads((out / "uncertainty.json").read_text())
            self.assertEqual(uncertainty["samples"], 5)

    def test_without_gcode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            completed = self.run_cli(
                "--probe",
                str(PROBE),
                "--out",
                str(out),
                "--resolution",
                "0.2",
                "--samples",
                "4",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            process = json.loads((out / "process.json").read_text())
            self.assertEqual(process["summary"]["match"]["source"], "default")
            self.assertEqual(process["summary"]["match"]["coverage"], 0.0)

    def test_missing_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = self.run_cli(
                "--probe",
                str(Path(tmp) / "missing.json"),
                "--out",
                str(Path(tmp) / "out"),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("error:", completed.stderr)


if __name__ == "__main__":
    unittest.main()
