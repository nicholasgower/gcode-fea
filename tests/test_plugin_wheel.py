from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helper_scripts"))

from build_plugin_wheel import build_wheel


class PluginWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wheel = build_wheel(out_dir=Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_contents(self) -> None:
        with zipfile.ZipFile(self.wheel) as archive:
            names = archive.namelist()
        self.assertIn("gcode_fea_plugin/__init__.py", names)
        self.assertIn("gcode_fea/__init__.py", names)
        self.assertIn("gcode_fea/config.py", names)
        self.assertIn("gcode_fea/plugin_export.py", names)
        self.assertIn("gcode_fea/step_planar.py", names)
        self.assertIn("gcode_fea/save_dialog.py", names)
        self.assertIn("gcode_fea/transfer.py", names)
        info = [name for name in names if name.endswith(".dist-info/METADATA")]
        self.assertEqual(len(info), 1)
        for required in ("METADATA", "WHEEL", "RECORD"):
            self.assertTrue(
                any(name.endswith(f".dist-info/{required}") for name in names)
            )
        self.assertTrue(
            any("licenses/LICENSE" in name for name in names)
        )
        self.assertTrue(
            any("licenses/CREDITS.md" in name for name in names)
        )

    def test_metadata(self) -> None:
        with zipfile.ZipFile(self.wheel) as archive:
            metadata = archive.read(
                [
                    name
                    for name in archive.namelist()
                    if name.endswith(".dist-info/METADATA")
                ][0]
            ).decode()
        self.assertIn("Name: gcode_fea_orca", metadata)
        self.assertIn("Requires-Dist: numpy", metadata)
        self.assertIn("Requires-Dist: shapely", metadata)
        self.assertIn("Requires-Dist: manifold3d", metadata)
        self.assertIn("Import-Name: gcode_fea_plugin", metadata)

    def test_record_hashes(self) -> None:
        with zipfile.ZipFile(self.wheel) as archive:
            record_name = [
                name for name in archive.namelist() if name.endswith(".dist-info/RECORD")
            ][0]
            lines = archive.read(record_name).decode().strip().splitlines()
            for line in lines:
                if line.endswith(","):
                    continue
                name, digest, size = line.rsplit(",", 2)
                data = archive.read(name)
                expected = base64.urlsafe_b64encode(
                    hashlib.sha256(data).digest()
                ).rstrip(b"=").decode()
                self.assertEqual(digest, f"sha256={expected}")
                self.assertEqual(int(size), len(data))

    def test_import_from_wheel(self) -> None:
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(self.wheel)!r}); "
            "from gcode_fea.config import PluginConfig; "
            "print(PluginConfig().resolution)"
        )
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "0.1")


if __name__ == "__main__":
    unittest.main()
