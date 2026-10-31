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


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "gcode_fea.cli", *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_export_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            completed = self.run_cli(
                "--probe",
                str(PROBE),
                "--out",
                str(out),
                "--resolution",
                "0.2",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((out / "report.json").read_text())
            sidecar = json.loads((out / "beads.json").read_text())
            self.assertEqual(report["summary"]["bead_count"], 3)
            self.assertEqual(len(sidecar["beads"]), 3)
            self.assertIn("content_hash", report)

    def test_content_hash_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            for out in (first, second):
                completed = self.run_cli(
                    "--probe",
                    str(PROBE),
                    "--out",
                    str(out),
                    "--resolution",
                    "0.2",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            report_a = json.loads((first / "report.json").read_text())
            report_b = json.loads((second / "report.json").read_text())
            self.assertEqual(report_a["content_hash"], report_b["content_hash"])

    def test_planar_step_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            step = out / "beads.step"
            completed = self.run_cli(
                "--probe",
                str(PROBE),
                "--out",
                str(out),
                "--resolution",
                "0.2",
                "--step",
                str(step),
                "--step-engine",
                "planar",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(step.exists())
            text = step.read_text()
            self.assertTrue(text.startswith("ISO-10303-21;"))
            self.assertIn("MANIFOLD_SOLID_BREP", text)
            self.assertIn("planar", completed.stdout)

    def test_missing_probe_fails(self) -> None:
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
