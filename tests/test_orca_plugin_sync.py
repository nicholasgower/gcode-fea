from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "helper_scripts" / "orca_plugin_sync.py"


class OrcaPluginSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        (self.config / "orca_plugins").mkdir(parents=True)
        self.src = self.root / "plugin_src"
        self.src.mkdir()
        (self.src / "dummy.py").write_text("# dummy plugin entry\n")
        self.dest = self.config / "orca_plugins" / "dummy"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_sync(
        self,
        *args: str,
        src: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--config-root",
                str(self.config),
                "--src",
                str(src or self.src),
                "--name",
                "dummy",
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def test_dry_run_writes_nothing(self) -> None:
        completed = self.run_sync("--dry-run")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("would copy", completed.stdout)
        self.assertFalse(self.dest.exists())

    def test_copy(self) -> None:
        completed = self.run_sync()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((self.dest / "dummy.py").is_file())

    def test_overwrite_requires_yes(self) -> None:
        self.assertEqual(self.run_sync().returncode, 0)
        completed = self.run_sync()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--yes", completed.stderr)
        completed = self.run_sync("--yes")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_source_fails(self) -> None:
        completed = self.run_sync("--dry-run", src=self.root / "missing")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing plugin source", completed.stderr)

    def test_multiple_entry_files_fail(self) -> None:
        (self.src / "second.py").write_text("# second entry\n")
        completed = self.run_sync("--dry-run")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("multiple entry files", completed.stderr)

    def test_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HELPER), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("Copy a plugin", completed.stdout)


if __name__ == "__main__":
    unittest.main()
