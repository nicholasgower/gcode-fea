from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea import save_dialog


def completed(code: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class SaveDialogTests(unittest.TestCase):
    def test_zenity_save(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(0, "/tmp/chosen.step\n")
        ) as run:
            result = save_dialog.choose_save_file(
                "/tmp/default.step", title="Save STEP"
            )
        self.assertTrue(result.available)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.backend, "zenity")
        self.assertEqual(result.path, Path("/tmp/chosen.step"))
        command = run.call_args.args[0]
        self.assertIn("--save", command)
        self.assertIn("--confirm-overwrite", command)
        self.assertIn("*.step", " ".join(command))

    def test_cancel(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(1)
        ):
            result = save_dialog.choose_save_file("/tmp/default.step")
        self.assertTrue(result.cancelled)
        self.assertIsNone(result.path)

    def test_extension_is_added(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(0, "/tmp/noext\n")
        ):
            result = save_dialog.choose_save_file("/tmp/default.step")
        self.assertEqual(result.path, Path("/tmp/noext.step"))

    def test_no_backend(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", return_value=None
        ):
            result = save_dialog.choose_save_file("/tmp/default.step")
        self.assertEqual(result.backend, "none")
        self.assertFalse(result.available)
        self.assertFalse(result.cancelled)
        self.assertIsNone(result.path)

    def test_missing_helper(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", side_effect=FileNotFoundError
        ):
            result = save_dialog.choose_save_file("/tmp/default.step")
        self.assertEqual(result.backend, "none")
        self.assertFalse(result.available)

    def test_kdialog(self) -> None:
        def which(name):
            return "/usr/bin/kdialog" if name == "kdialog" else None

        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=which
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(0, "/tmp/k.step\n")
        ) as run:
            result = save_dialog.choose_save_file("/tmp/default.step")
        self.assertEqual(result.backend, "kdialog")
        self.assertEqual(result.path, Path("/tmp/k.step"))
        self.assertIn("--getsavefilename", run.call_args.args[0])

    def test_directory_chooser(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(0, "/tmp/exports\n")
        ) as run:
            result = save_dialog.choose_directory("/tmp/exports/beads.step")
        self.assertEqual(result.path, Path("/tmp/exports"))
        command = run.call_args.args[0]
        self.assertIn("--directory", command)

    def test_macos(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "darwin"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/osascript"
        ), mock.patch.object(
            save_dialog.subprocess, "run", return_value=completed(0, "/Users/a/x.step\n")
        ) as run:
            result = save_dialog.choose_save_file("/Users/a/default.step")
        self.assertEqual(result.backend, "macos")
        self.assertIn("osascript", run.call_args.args[0][0])
        self.assertEqual(result.path, Path("/Users/a/x.step"))

    def test_windows(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "win32"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: r"C:\powershell.exe"
        ), mock.patch.object(
            save_dialog.subprocess, "run",
            return_value=completed(0, r"C:\out\beads.step" + "\n"),
        ) as run:
            result = save_dialog.choose_save_file(r"C:\out\beads.step")
        self.assertEqual(result.backend, "windows")
        self.assertEqual(result.path, Path(r"C:\out\beads.step"))
        self.assertIn("SaveFileDialog", run.call_args.args[0][-1])

    def test_timeout(self) -> None:
        with mock.patch.object(save_dialog.sys, "platform", "linux"), mock.patch.object(
            save_dialog.shutil, "which", side_effect=lambda name: "/usr/bin/zenity"
        ), mock.patch.object(
            save_dialog.subprocess, "run", side_effect=subprocess.TimeoutExpired("zenity", 1)
        ):
            result = save_dialog.choose_save_file("/tmp/default.step", timeout=1)
        self.assertTrue(result.cancelled)
        self.assertIsNone(result.path)


if __name__ == "__main__":
    unittest.main()
