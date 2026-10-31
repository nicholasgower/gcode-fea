from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "helper_scripts" / "orca_env.py"


class OrcaEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.appimage = self.root / "OrcaSlicer.AppImage"
        self.appimage.write_text("")
        self.appimage.chmod(0o755)
        self.config = self.root / "config"
        (self.config / "orca_plugins").mkdir(parents=True)
        log_dir = self.config / "log"
        log_dir.mkdir()
        (log_dir / "debug_test.log").write_text(
            "[info] 2026-01-01 00:00:00: Current OrcaSlicer Version "
            "2.5.0-beta build 345efec6\n"
        )
        (log_dir / "python_test.log").write_text("")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_env(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--appimage",
                str(self.appimage),
                "--config-root",
                str(self.config),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def test_json_ok(self) -> None:
        completed = self.run_env("--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertTrue(data["ok"])
        checks = {check["name"]: check for check in data["checks"]}
        for name in ("appimage", "config_root", "plugin_dir", "fixtures", "launch_log"):
            self.assertEqual(checks[name]["status"], "PASS", checks[name])

    def test_missing_appimage_fails(self) -> None:
        missing = self.root / "missing.AppImage"
        completed = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--appimage",
                str(missing),
                "--config-root",
                str(self.config),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        data = json.loads(completed.stdout)
        self.assertFalse(data["ok"])
        checks = {check["name"]: check for check in data["checks"]}
        self.assertEqual(checks["appimage"]["status"], "FAIL")

    def test_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HELPER), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("Read-only doctor", completed.stdout)


if __name__ == "__main__":
    unittest.main()
