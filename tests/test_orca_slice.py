from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "helper_scripts" / "orca_slice.py"
MODEL = REPO_ROOT / "tests" / "fixtures" / "models" / "cube.stl"
SETTINGS = REPO_ROOT / "tests" / "fixtures" / "profiles" / "process_headless.json"


class OrcaSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.appimage = self.root / "OrcaSlicer.AppImage"
        self.appimage.write_text("")
        self.appimage.chmod(0o755)
        self.out = self.root / "out"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_slice(
        self, *args: str, appimage: Path | None = None, model: Path | None = None,
        settings: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--appimage",
                str(appimage or self.appimage),
                "--model",
                str(model or MODEL),
                "--settings",
                str(settings or SETTINGS),
                "--out",
                str(self.out),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def test_dry_run_writes_nothing(self) -> None:
        completed = self.run_slice("--dry-run")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--appimage-extract-and-run", completed.stdout)
        self.assertIn("--slice 0", completed.stdout)
        self.assertIn("--outputdir", completed.stdout)
        self.assertIn("--logfile", completed.stdout)
        self.assertNotIn("APPIMAGE_EXTRACT_AND_RUN", completed.stdout)
        self.assertFalse(self.out.exists())

    def test_missing_appimage_fails(self) -> None:
        completed = self.run_slice(
            "--dry-run", appimage=self.root / "missing.AppImage"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing AppImage", completed.stderr)

    def test_missing_model_fails(self) -> None:
        completed = self.run_slice("--dry-run", model=self.root / "missing.stl")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing model", completed.stderr)

    def test_invalid_settings_fail(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{}")
        completed = self.run_slice("--dry-run", settings=bad)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not a process preset", completed.stderr)

    def test_dry_run_and_verify_are_exclusive(self) -> None:
        completed = self.run_slice("--dry-run", "--verify")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("mutually exclusive", completed.stderr)

    def test_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HELPER), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("OrcaSlicer 2.5", completed.stdout)


if __name__ == "__main__":
    unittest.main()
