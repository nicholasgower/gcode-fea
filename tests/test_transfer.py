from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea import transfer


class TransferTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "posix copy test")
    def test_copy_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.step"
            source.write_text("step data")
            destination = Path(tmp) / "nested" / "target.step"
            transfer.copy_file(source, destination)
            self.assertEqual(destination.read_text(), "step data")

    @unittest.skipUnless(sys.platform.startswith("linux"), "posix copy test")
    def test_copy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "stage"
            source.mkdir()
            (source / "a.json").write_text("a")
            (source / "b.step").write_text("b")
            destination = Path(tmp) / "chosen"
            transfer.copy_directory(source, destination)
            self.assertEqual((destination / "a.json").read_text(), "a")
            self.assertEqual((destination / "b.step").read_text(), "b")

    def test_command_is_constant_and_paths_go_in_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "beads.step"
            source.write_text("x")
            destination = Path(tmp) / "out" / "beads.step"
            with mock.patch.object(
                transfer.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ) as run:
                transfer.copy_file(source, destination)
            command = run.call_args.args[0]
            env = run.call_args.kwargs["env"]
            self.assertEqual(command[0], "sh")
            self.assertIn("ORCA_FEA_SRC", command[-1])
            self.assertEqual(env["ORCA_FEA_SRC"], str(source))
            self.assertEqual(env["ORCA_FEA_DST"], str(destination))

    def test_process_commands_are_declared(self) -> None:
        commands = transfer.process_commands()
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0], "sh" if not sys.platform.startswith("win") else "cmd")
        self.assertTrue(all(command for command in commands))

    def test_copy_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "missing.step"
            with self.assertRaises(FileNotFoundError):
                transfer.copy_file(source, Path(tmp) / "out.step")

    def test_helper_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "beads.step"
            source.write_text("x")
            with mock.patch.object(
                transfer.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="nope"
                ),
            ):
                with self.assertRaises(RuntimeError):
                    transfer.copy_file(source, Path(tmp) / "out.step")


if __name__ == "__main__":
    unittest.main()
